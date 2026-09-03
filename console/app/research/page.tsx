"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { ArrowDown, Plus, RefreshCw, Search } from "lucide-react";
import {
  ActionButton,
  Badge,
  Card,
  ConfirmationDialog,
  DataBoundary,
  DescriptionList,
  Disclosure,
  ErrorNote,
  Empty,
  FormActions,
  FormField,
  HorizontalTabs,
  PageActionMenu,
  QuickLink,
  TextInputDialog,
  displayJson,
  formatDate,
  shortId,
} from "../components/ui";
import { ConsoleShell } from "../components/console-shell";
import { envelopeData, listOf, postApi, useApi } from "../lib/api";
import { useAgentPageContext } from "../lib/agent-page-context";
import { notifyConsole } from "../lib/notifications";
import { ResearchContinuity } from "./research-continuity";
import { textDash as text } from "../lib/coerce";
import { EntityBrowser } from "../components/entity-browser";

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

function optionLabel(value: string): string {
  return value.split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}
const INVALIDATION_SEVERITIES = ["soft", "hard"];
const ATTACHED_INSTRUMENT_STATUSES = new Set(["watching", "shortlisted", "selected"]);
const RESEARCH_MODULES = [
  { key: "overview", label: "Overview" },
  { key: "instruments", label: "Instruments" },
  { key: "thesis", label: "Thesis" },
  { key: "evidence", label: "Evidence" },
  { key: "trade-plan", label: "Trade Plan" },
  { key: "history", label: "History" },
  { key: "monitors", label: "Monitors" },
] as const;
type ResearchModule = (typeof RESEARCH_MODULES)[number]["key"];
const RESEARCH_MODULE_KEYS = new Set<string>(RESEARCH_MODULES.map((module) => module.key));
const RESEARCH_PAGE_SIZE = {
  initial: 6,
  // The legacy observer measured the outer Research index. EntityBrowser sits
  // inside the 16px card padding and keeps the same effective breakpoints.
  getSize: (width: number) => { const outerWidth = width + 34; return outerWidth >= 900 ? 6 : outerWidth >= 720 ? 5 : outerWidth >= 560 ? 4 : outerWidth >= 420 ? 3 : outerWidth >= 300 ? 2 : 1; },
  target: "container" as const,
};

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

function Field(props: { label: string; children: ReactNode; className?: string; required?: boolean }) {
  return <FormField {...props} className={`research-field ${props.className ?? ""}`} />;
}

type InstrumentSuggestion = { instrument_id: string; symbol: string; name: string; market: string; asset_type: string; exchange?: string };
const CANDIDATE_MARKETS = ["US", "A_SHARE", "KR", "CME", "OTC"];

function instrumentMarket(instrumentId: unknown): string {
  const market = String(instrumentId ?? "").split(":")[1];
  return CANDIDATE_MARKETS.includes(market) ? market : "US";
}

function instrumentSuggestions(envelope: Dict): InstrumentSuggestion[] {
  const data = envelope.data && typeof envelope.data === "object" ? envelope.data as Dict : null;
  const instrument = data?.instrument && typeof data.instrument === "object" ? data.instrument as Dict : null;
  const candidates = Array.isArray(data?.candidates) ? data.candidates : [];
  const firstError = Array.isArray(envelope.errors) ? envelope.errors[0] as Dict | undefined : undefined;
  const details = firstError?.details && typeof firstError.details === "object" ? firstError.details as Dict : null;
  const previews = Array.isArray(details?.candidates_preview) ? details.candidates_preview : [];
  const source = instrument ? [instrument, ...candidates, ...previews] : [...candidates, ...previews];
  const unique = new Map<string, InstrumentSuggestion>();
  source.forEach((item) => {
    if (!item || typeof item !== "object") return;
    const value = item as Dict;
    const instrumentId = text(value.instrument_id);
    if (!instrumentId) return;
    unique.set(instrumentId, { instrument_id: instrumentId, symbol: text(value.symbol), name: text(value.name, text(value.symbol, instrumentId)), market: text(value.market), asset_type: text(value.asset_type), exchange: text(value.exchange) });
  });
  return [...unique.values()].slice(0, 10);
}

function SubjectEditor({
  draft,
  editing,
  busy,
  error,
  onChange,
  onCancel,
  onSave,
}: {
  draft: SubjectDraft;
  editing: boolean;
  busy: boolean;
  error?: string | null;
  onChange: (next: SubjectDraft) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const primaryInstrumentRequired = draft.subjectType === "company" || draft.subjectType === "catalyst";
  return (
    <Card className="research-editor-card" kicker={editing ? "SUBJECT METADATA · AUDITED UPDATE" : "RESEARCH SUBJECT"} title={editing ? "Edit Research Subject Metadata" : "Create Research Subject"}>
      <p className="card-note">{editing ? "Title, summary, tags, and links are editable metadata. Research Subject Type and Primary Instrument define the durable identity and cannot change after creation." : "Create the durable research identity. Later metadata writes leave an auditable confirmation record and do not modify Thesis revisions or positions."}</p>
      <div className="research-form-grid">
        <Field label="Research Subject Type" required={!editing} className={editing ? "research-field-immutable" : ""}><select value={draft.subjectType} required={!editing} disabled={editing} onChange={(event) => onChange({ ...draft, subjectType: event.target.value })}>{SUBJECT_TYPES.map((value) => <option key={value} value={value}>{optionLabel(value)}</option>)}</select></Field>
        <Field label="Primary Instrument ID" required={!editing && primaryInstrumentRequired} className={editing ? "research-field-immutable" : ""}><input value={draft.instrument} required={!editing && primaryInstrumentRequired} disabled={editing} onChange={(event) => onChange({ ...draft, instrument: event.target.value })} placeholder={draft.subjectType === "theme" ? "Leave blank" : "equity:US:NVDA"} /></Field>
        <Field label="Title" className="research-field-wide" required><input value={draft.title} required onChange={(event) => onChange({ ...draft, title: event.target.value })} placeholder="e.g. NVDA AI infrastructure tracking" /></Field>
        <Field label="Summary" className="research-field-wide" required><textarea value={draft.summary} required onChange={(event) => onChange({ ...draft, summary: event.target.value })} rows={5} placeholder="Record the long-term question, scope, and boundaries for this Research Subject." /></Field>
        <Field label="Topic Tags"><input value={draft.tags} onChange={(event) => onChange({ ...draft, tags: event.target.value })} placeholder="ai, valuation, catalyst" /></Field>
        <Field label="Linked Research Subject IDs"><textarea value={draft.linkedSubjectIds} onChange={(event) => onChange({ ...draft, linkedSubjectIds: event.target.value })} rows={2} placeholder="One case_<uuid7> per line" /></Field>
      </div>
      <ErrorNote role="alert">{error}</ErrorNote>
      <FormActions className="research-form-actions"><ActionButton onClick={onSave} busy={busy}>{editing ? "Save Research Subject" : "Create Research Subject"}</ActionButton><button className="close-button" type="button" onClick={onCancel}>Cancel</button></FormActions>
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
  error,
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
  error?: string | null;
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
    <section className="research-thesis-editor" aria-label={thesisId ? "Edit Thesis Revision" : "Create Thesis"}>
      <div className="research-thesis-editor-heading"><div><p className="card-kicker">{thesisId ? "THESIS REVISION · APPEND ONLY" : "THESIS · CANDIDATE"}</p><h3>{thesisId ? "Edit Thesis · Propose New Revision" : "Create New Thesis"}</h3></div><Badge value={subjectStatus.toUpperCase()} /></div>
      <p className="card-note">Historical revisions are never overwritten. Saving creates a pending candidate that must be explicitly Confirmed or Rejected.</p>
      <div className="research-form-grid">
        <Field label="Title" className="research-field-wide" required><input value={draft.title} required onChange={(event) => onChange({ ...draft, title: event.target.value })} /></Field>
        <Field label="Thesis Role" required><select value={draft.thesisRole} required onChange={(event) => onChange({ ...draft, thesisRole: event.target.value, parentThesisId: event.target.value === "sub" ? draft.parentThesisId : "" })}>{THESIS_ROLES.map((value) => <option key={value} value={value}>{optionLabel(value)}</option>)}</select></Field>
        {draft.thesisRole === "sub" && <Field label="Parent PRIMARY Thesis" required><select value={draft.parentThesisId} required onChange={(event) => onChange({ ...draft, parentThesisId: event.target.value, rivalThesisIds: draft.rivalThesisIds.filter((id) => id !== event.target.value) })}><option value="">Select a Parent Thesis</option>{primaryTargets.map((item) => <option value={text(item.thesis_id)} key={text(item.thesis_id)}>{text(item.title, "Unnamed PRIMARY")} · {text(item.status)}</option>)}</select></Field>}
        <Field label="Rival Theses" className="research-field-wide"><div className="research-thesis-relation-options">{relationshipTargets.length === 0 ? <span className="muted">No other Theses available.</span> : relationshipTargets.map((item) => { const id = text(item.thesis_id); const disabled = id === draft.parentThesisId; return <label key={id}><input type="checkbox" checked={draft.rivalThesisIds.includes(id)} disabled={disabled} onChange={(event) => onChange({ ...draft, rivalThesisIds: event.target.checked ? [...draft.rivalThesisIds, id] : draft.rivalThesisIds.filter((value) => value !== id) })} /><span>{text(item.title, "Unnamed Thesis")} · {text(item.role).toUpperCase()} · {text(item.status)}</span></label>; })}</div><small>Use this to declare competing explanations or contrary judgments; a parent Thesis cannot also be marked as a rival.</small></Field>
        <Field label="Candidate Status" required><div className="research-status-control">{thesisId && <label className="research-status-toggle"><input type="checkbox" checked={statusExplicit} onChange={(event) => onStatusExplicitChange(event.target.checked)} /><span>Also Update Status</span></label>}<select value={draft.thesisStatus} required disabled={Boolean(thesisId) && !statusExplicit} onChange={(event) => onChange({ ...draft, thesisStatus: event.target.value })}>{THESIS_STATUSES.map((value) => <option key={value} value={value}>{optionLabel(value)}</option>)}</select></div></Field>
        <Field label="Confidence" required><select value={draft.confidenceBand} required onChange={(event) => onChange({ ...draft, confidenceBand: event.target.value })}>{CONFIDENCE_BANDS.map((value) => <option key={value} value={value}>{optionLabel(value)}</option>)}</select></Field>
        <Field label="Rating" required><select value={draft.rating} required onChange={(event) => onChange({ ...draft, rating: event.target.value })}>{RATINGS.map((value) => <option key={value} value={value}>{optionLabel(value)}</option>)}</select></Field>
        <Field label="Replacement Revision No"><input inputMode="numeric" value={draft.replacesRevisionNo} onChange={(event) => onChange({ ...draft, replacesRevisionNo: event.target.value.replace(/[^0-9]/g, "") })} placeholder="Current Revision No" /></Field>
        <Field label="Statement" className="research-field-wide" required><textarea value={draft.statement} required onChange={(event) => onChange({ ...draft, statement: event.target.value })} rows={5} /></Field>
        <Field label="Rationale" className="research-field-wide" required><textarea value={draft.rationale} required onChange={(event) => onChange({ ...draft, rationale: event.target.value })} rows={5} /></Field>
        <Field label="Invalidation Check Note" className="research-field-wide" required><textarea value={draft.invalidationCheckNote} required onChange={(event) => onChange({ ...draft, invalidationCheckNote: event.target.value })} rows={4} /></Field>
      </div>
      {subjectStatus === "draft" && ["active", "strengthened", "weakened"].includes(draft.thesisStatus) && (!thesisId || statusExplicit) && <div className="research-state-warning" role="status">A Draft Research Subject cannot confirm a live Thesis. Submit and confirm a Research Subject activation candidate first, or keep Thesis status at DRAFT.</div>}
      <div className="research-array-editor">
        <div className="research-array-heading"><div><p className="card-kicker">THESIS PREMISES</p><h3>Assumptions</h3></div><button className="close-button" type="button" onClick={() => onChange({ ...draft, assumptions: [...draft.assumptions, { statement: "", basis: "", falsifiability: "" }] })}>Add Assumption</button></div>
        {draft.assumptions.length === 0 ? <p className="muted">No assumptions yet; add at least one if this judgment should be challenged over time.</p> : draft.assumptions.map((item, index) => <div className="research-array-row" key={`assumption-${index}`}><Field label="Statement"><textarea rows={3} value={item.statement} onChange={(event) => updateAssumption(index, "statement", event.target.value)} /></Field><Field label="Basis"><textarea rows={3} value={item.basis} onChange={(event) => updateAssumption(index, "basis", event.target.value)} /></Field><Field label="Falsifiability"><textarea rows={3} value={item.falsifiability} onChange={(event) => updateAssumption(index, "falsifiability", event.target.value)} /></Field><button className="close-button" type="button" onClick={() => onChange({ ...draft, assumptions: draft.assumptions.filter((_, itemIndex) => itemIndex !== index) })}>Remove</button></div>)}
      </div>
      <div className="research-array-editor">
        <div className="research-array-heading"><div><p className="card-kicker">FALSIFIABILITY</p><h3>Invalidation Conditions</h3></div><button className="close-button" type="button" onClick={() => onChange({ ...draft, invalidations: [...draft.invalidations, { description: "", observable: "", severity: "soft" }] })}>Add Condition</button></div>
        {draft.invalidations.length === 0 ? <p className="muted">No invalidation conditions yet.</p> : draft.invalidations.map((item, index) => <div className="research-array-row" key={`invalidation-${index}`}><Field label="Description"><textarea rows={3} value={item.description} onChange={(event) => updateInvalidation(index, "description", event.target.value)} /></Field><Field label="Observable"><textarea rows={3} value={item.observable} onChange={(event) => updateInvalidation(index, "observable", event.target.value)} /></Field><Field label="Severity"><select value={item.severity} onChange={(event) => updateInvalidation(index, "severity", event.target.value)}>{INVALIDATION_SEVERITIES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field><button className="close-button" type="button" onClick={() => onChange({ ...draft, invalidations: draft.invalidations.filter((_, itemIndex) => itemIndex !== index) })}>Remove</button></div>)}
      </div>
      <ErrorNote role="alert">{error}</ErrorNote>
      <FormActions className="research-form-actions"><ActionButton onClick={onSave} busy={busy}>Propose Candidate</ActionButton><button className="close-button" type="button" onClick={onCancel}>Cancel</button></FormActions>
    </section>
  );
}

function MonitorLinks({ monitorData, subjectId, loading }: { monitorData: Dict | null; subjectId: string; loading: boolean }) {
  const dashboard = envelopeData<Dict>(monitorData?.dashboard);
  const monitorItems = listOf<MonitorAggregate>(dashboard, "items");
  const linked = monitorItems.map((item) => item.monitor ?? item).filter((monitor) => text(monitor.subject_id, "") === subjectId);
  return (
    <Card className="research-related-card" kicker="MONITORING" title="Linked Monitors" description="Definitions bound to this Research Subject." action={<QuickLink className="text-link" href="/monitors">Open Monitor workspace</QuickLink>}>
      {loading ? <Empty>Reading Monitor links…</Empty> : linked.length === 0 ? <Empty>This Research Subject has no linked Monitors. Bind one by exact Research Subject ID in the Monitor workspace.</Empty> : <div className="research-monitor-links">{linked.map((monitor) => <Link href={`/monitors#monitor-${text(monitor.monitor_id)}`} className="research-monitor-link" key={text(monitor.monitor_id)}><div><strong>{text(monitor.name, "Unnamed Monitor")}</strong><small>{shortId(monitor.primary_instrument_id)} · {text(monitor.cadence)}</small></div><Badge value={text(monitor.status, "UNKNOWN")} /></Link>)}</div>}
    </Card>
  );
}

function ThesisSummary({ thesis, revision, assumptions, invalidations }: { thesis: Dict; revision?: Dict; assumptions: Dict[]; invalidations: Dict[] }) {
  return (
    <article className="research-thesis-summary">
      <header><div><strong>{text(thesis.title, "Unnamed Thesis")}</strong><small className="mono">{text(thesis.thesis_id)}</small></div><div className="research-thesis-badges"><Badge value={text(thesis.status, "UNKNOWN").toUpperCase()} /><span className="research-role">{text(thesis.role).toUpperCase()}</span></div></header>
      <div className="research-thesis-facts"><div><span>Created</span><strong>{formatDate(thesis.created_at)}</strong></div><div><span>Status</span><strong>{text(thesis.status).toUpperCase()}</strong></div><div><span>Role</span><strong>{text(thesis.role).toUpperCase()}</strong></div><div><span>Rating</span><strong>{text(revision?.rating).toUpperCase()}</strong></div><div><span>Confidence</span><strong>{text(revision?.confidence_band).toUpperCase()}</strong></div><div><span>Current Revision</span><strong>v{text(thesis.current_revision_no)}</strong><small>latest v{text(revision?.revision_no)} · {formatDate(revision?.created_at)}</small></div></div>
      <section className="research-latest-revision"><header><div><span>LATEST REVISION</span><strong>Statement & Supporting Judgment</strong></div><Badge value={`V${text(revision?.revision_no, text(thesis.current_revision_no))}`} /></header><p className="research-latest-statement">{text(revision?.statement, "No latest revision statement.")}</p>{revision && <div className="research-thesis-detail-grid"><div><span>Rationale</span><p>{text(revision.rationale)}</p></div><div><span>Invalidation Check</span><p>{text(revision.invalidation_check_note)}</p></div></div>}</section>
      {(text(thesis.parent_thesis_id, "") || stringList(thesis.rival_thesis_ids).length > 0) && <div className="research-thesis-relations">{text(thesis.parent_thesis_id, "") && <span>Parent Thesis · <code>{shortId(thesis.parent_thesis_id)}</code></span>}{stringList(thesis.rival_thesis_ids).length > 0 && <span>Rivals · {stringList(thesis.rival_thesis_ids).map((id) => <code key={id}>{shortId(id)}</code>)}</span>}</div>}
      <div className="research-inline-columns"><div><span>Assumptions · {assumptions.length}</span>{assumptions.length === 0 ? <small className="muted">None</small> : <ul>{assumptions.map((item) => <li key={text(item.assumption_id)}>{text(item.statement)}</li>)}</ul>}</div><div><span>Invalidation Conditions · {invalidations.length}</span>{invalidations.length === 0 ? <small className="muted">None</small> : <ul>{invalidations.map((item) => <li key={text(item.invalidation_id)}>{text(item.description)}</li>)}</ul>}</div></div>
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

const CANDIDATE_NARRATIVE_FIELDS = ["statement", "rationale", "invalidation_check_note", "summary", "notes", "thesis_hint", "selection_reason"];

function candidateFieldLabel(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function candidateTitle(kind: string, payload: Dict): string {
  if (kind === "trade_plan") return `Trade Plan · ${shortId(payload.instrument_id)}`;
  if (kind === "thesis_revision") return `Thesis Revision · ${text(payload.title, "Untitled Thesis")}`;
  if (kind === "watchlist_item") return `Instrument · ${text(payload.display_name, shortId(payload.instrument_id))}`;
  if (kind === "subject_status_change" || kind === "case_status_change") return `Research Subject Status · ${text(payload.new_status, "UNKNOWN").toUpperCase()}`;
  return candidateFieldLabel(kind || "Candidate Proposal");
}

function CandidateCollection({ name, items }: { name: string; items: unknown[] }) {
  return <Disclosure className="research-candidate-collection" variant="compact" defaultOpen title={<>{candidateFieldLabel(name)} · {items.length}</>}><div>{items.map((item, index) => {
    const value = item && typeof item === "object" ? item as Dict : null;
    if (!value) return <article key={`${name}-${index}`}><strong>{text(item)}</strong></article>;
    const description = text(value.description, text(value.statement, text(value.condition_code, `${candidateFieldLabel(name)} ${index + 1}`)));
    const condition = [value.metric_key, value.comparator, value.threshold].filter((entry) => entry !== null && entry !== undefined && entry !== "").map(String).join(" ");
    const context = [value.phase, value.mode, value.fact_type, value.instrument_id, value.observable, value.severity].filter((entry) => entry !== null && entry !== undefined && entry !== "").map(String).join(" · ");
    return <article key={`${name}-${index}`}><strong>{description}</strong>{context && <small>{context}</small>}{condition && <code>{condition}</code>}{value.basis ? <p><span>Basis</span>{text(value.basis)}</p> : null}{value.falsifiability ? <p><span>Falsifiability</span>{text(value.falsifiability)}</p> : null}</article>;
  })}</div></Disclosure>;
}

function CandidateReviewDetails({ candidate, payload, kind }: { candidate: Dict; payload: Dict; kind: string }) {
  const collectionEntries = Object.entries(payload).filter(([, value]) => Array.isArray(value) && value.length > 0) as Array<[string, unknown[]]>;
  const scalarItems = Object.entries(payload)
    .filter(([key, value]) => key !== "kind" && !CANDIDATE_NARRATIVE_FIELDS.includes(key) && !Array.isArray(value) && (value === null || typeof value !== "object"))
    .map(([key, value]) => ({ label: candidateFieldLabel(key), value: value === null || value === "" ? "—" : typeof value === "boolean" ? (value ? "Yes" : "No") : String(value) }));
  const narratives = CANDIDATE_NARRATIVE_FIELDS.map((key) => [key, payload[key]] as const).filter(([, value]) => typeof value === "string" && value.trim());
  return <section className="research-candidate-review" aria-label="Candidate Review Details">
    <DescriptionList columns={4} items={[
      { label: "Candidate ID", value: text(candidate.candidate_id) },
      { label: "Proposal Type", value: candidateFieldLabel(kind) },
      { label: "Proposed", value: formatDate(candidate.proposed_at) },
      { label: "Expires", value: formatDate(candidate.expires_at) },
      { label: "Proposed By", value: text(candidate.proposed_by, "unknown") },
      { label: "Confirmation Mode", value: text(candidate.confirmation_mode, "UNKNOWN").toUpperCase() },
      { label: "Subject", value: shortId(candidate.subject_id) },
      { label: "Thesis", value: shortId(candidate.thesis_id) },
    ]} />
    {text(candidate.proposed_by_rationale, "") ? <div className="research-candidate-rationale"><span>Why This Change Was Proposed</span><p>{text(candidate.proposed_by_rationale, "")}</p></div> : null}
    {scalarItems.length > 0 && <DescriptionList columns={4} className="research-candidate-payload-facts" items={scalarItems} />}
    {narratives.length > 0 && <div className="research-candidate-narratives">{narratives.map(([key, value]) => <section key={key}><span>{candidateFieldLabel(key)}</span><p>{String(value)}</p></section>)}</div>}
    {collectionEntries.map(([name, items]) => <CandidateCollection key={name} name={name} items={items} />)}
    <Disclosure className="research-candidate-raw" variant="code" title="Complete Proposal Payload"><pre>{displayJson(payload)}</pre></Disclosure>
  </section>;
}

function PendingCandidate({ candidate, subjectStatus, onDecision, busy }: { candidate: Dict; subjectStatus: string; onDecision: (candidate: Dict, action: "confirm" | "reject" | "withdraw", reason?: string) => Promise<void>; busy: boolean }) {
  const [rejectionReason, setRejectionReason] = useState("");
  const [rejectionError, setRejectionError] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const rejectionInputRef = useRef<HTMLInputElement>(null);
  const payload = candidate.payload && typeof candidate.payload === "object" ? candidate.payload as Dict : {};
  const kind = text(payload.kind, text(candidate.kind, "thesis_revision"));
  const isSubjectStatus = kind === "subject_status_change" || kind === "case_status_change";
  const isInstrumentCandidate = kind === "watchlist_item";
  const summary = isSubjectStatus
    ? `${subjectStatus.toUpperCase()} → ${text(payload.new_status, "UNKNOWN").toUpperCase()}`
    : isInstrumentCandidate
      ? payload.action === "create" ? `Add Instrument: ${text(payload.display_name)} · ${text(payload.instrument_id)}` : `Instrument Update: ${text(payload.new_status).toUpperCase()}${payload.selection_reason ? ` · ${text(payload.selection_reason)}` : ""}`
      : text(payload.statement, text(payload.title, candidateTitle(kind, payload)));
  async function submit(action: "confirm" | "reject" | "withdraw") {
    setDecisionError(null);
    if (action === "reject" && !rejectionReason.trim()) {
      setRejectionError("A rejection reason is required.");
      rejectionInputRef.current?.focus();
      return;
    }
    try {
      await onDecision(candidate, action, action === "reject" ? rejectionReason.trim() : undefined);
    } catch (cause) {
      setDecisionError(cause instanceof Error ? cause.message : "Candidate decision failed.");
    }
  }
  return <article className="research-candidate"><header><div><strong>{candidateTitle(kind, payload)}</strong><small>{text(candidate.candidate_id)} · proposed by {text(candidate.proposed_by, "unknown")}</small></div><Badge value={text(candidate.status, "PROPOSED").toUpperCase()} /></header><p>{summary}</p><CandidateReviewDetails candidate={candidate} payload={payload} kind={kind} /><ErrorNote role="alert">{decisionError}</ErrorNote><div className="research-candidate-decision"><section className="research-candidate-confirm"><div><strong>Approve This Exact Proposal</strong><small>{isInstrumentCandidate ? "Approval adds this Instrument directly to Instruments." : "Confirmation creates the next durable state or revision."}</small></div><ActionButton onClick={() => { void submit("confirm"); }} busy={busy}>{isInstrumentCandidate ? "Approve Instrument" : "Confirm Candidate"}</ActionButton></section><section className="research-candidate-decline"><label><span><b className="required-mark" aria-hidden="true">*</b>Reject with Rationale</span><input ref={rejectionInputRef} required value={rejectionReason} onChange={(event) => { setRejectionReason(event.target.value); setRejectionError(null); }} placeholder="Explain why this proposal should not proceed" aria-label="Candidate Rejection Reason" /></label><ErrorNote role="alert">{rejectionError}</ErrorNote><div><ActionButton tone="warning" onClick={() => { void submit("reject"); }} busy={busy}>Reject Candidate</ActionButton><button className="research-withdraw-button" type="button" disabled={busy} onClick={() => { void submit("withdraw"); }}>Withdraw Proposal</button></div></section></div></article>;
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
  const instrumentCandidates = listOf<Dict>(state, "watchlist_items").filter((candidate) => ATTACHED_INSTRUMENT_STATUSES.has(text(candidate.status, "")));
  const primaryInstrumentId = text(researchSubject.primary_instrument_id, "");
  const additionalInstrumentCandidates = instrumentCandidates.filter((candidate) => text(candidate.instrument_id, "") !== primaryInstrumentId);
  const instrumentInventory = [
    ...(primaryInstrumentId ? [{ instrumentId: primaryInstrumentId, displayName: shortId(primaryInstrumentId), status: "PRIMARY" }] : []),
    ...additionalInstrumentCandidates
      .map((candidate) => ({ instrumentId: text(candidate.instrument_id, `${text(candidate.market)}:${text(candidate.symbol)}`), displayName: text(candidate.display_name, shortId(candidate.instrument_id)), status: "INSTRUMENT" })),
  ];
  const pendingCandidates = listOf<Dict>(state, "pending_candidates").filter((candidate) => (
    candidate._truncated !== true
    && typeof candidate.candidate_id === "string"
    && candidate.candidate_id.length > 0
  ));
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
  const [candidateMarket, setCandidateMarket] = useState(() => instrumentMarket(researchSubject.primary_instrument_id));
  const [candidateInstrumentQuery, setCandidateInstrumentQuery] = useState("");
  const [candidateInstrumentId, setCandidateInstrumentId] = useState("");
  const [candidateDisplayName, setCandidateDisplayName] = useState("");
  const [candidateThesisHint, setCandidateThesisHint] = useState("");
  const [candidateSuggestions, setCandidateSuggestions] = useState<InstrumentSuggestion[]>([]);
  const [candidateSuggestionsOpen, setCandidateSuggestionsOpen] = useState(false);
  const [candidateResolving, setCandidateResolving] = useState(false);
  const [candidateResolveMessage, setCandidateResolveMessage] = useState<string | null>(null);
  const [candidateProposalError, setCandidateProposalError] = useState<string | null>(null);
  const [candidateProposalSuccess, setCandidateProposalSuccess] = useState<string | null>(null);
  const [activeModule, setActiveModule] = useState<ResearchModule>("overview");
  const [detailError, setDetailError] = useState<string | null>(null);
  const [archiveConfirmation, setArchiveConfirmation] = useState(false);
  const [restoreConfirmation, setRestoreConfirmation] = useState(false);
  const [archiveReasonOpen, setArchiveReasonOpen] = useState(false);
  const [archiveReason, setArchiveReason] = useState("Research scope ended or judgment invalidated");
  const [archiveReasonError, setArchiveReasonError] = useState<string | null>(null);

  useEffect(() => {
    const syncModuleFromUrl = () => {
      const requested = new URLSearchParams(window.location.search).get("section");
      setActiveModule(RESEARCH_MODULE_KEYS.has(requested ?? "") ? requested as ResearchModule : "overview");
    };
    syncModuleFromUrl();
    window.addEventListener("popstate", syncModuleFromUrl);
    return () => window.removeEventListener("popstate", syncModuleFromUrl);
  }, []);

  useEffect(() => {
    setSubjectDraft(subjectDraftFrom(researchSubject));
    setSubjectEditor(false);
    setThesisEditor(false);
    setThesisId(null);
    setThesisStatusExplicit(false);
    setCandidateMarket(instrumentMarket(researchSubject.primary_instrument_id));
    setCandidateInstrumentQuery("");
    setCandidateInstrumentId("");
    setCandidateDisplayName("");
    setCandidateThesisHint("");
    setCandidateSuggestions([]);
    setCandidateSuggestionsOpen(false);
    setCandidateResolving(false);
    setCandidateResolveMessage(null);
    setCandidateProposalError(null);
    setCandidateProposalSuccess(null);
    setDetailError(null);
    setArchiveConfirmation(false);
    setRestoreConfirmation(false);
    setArchiveReasonOpen(false);
  }, [researchSubject.subject_id, researchSubject.updated_at]);

  useEffect(() => {
    const query = candidateInstrumentQuery.trim();
    if (!query || (candidateInstrumentId && query === candidateInstrumentId)) {
      setCandidateResolving(false);
      setCandidateResolveMessage(null);
      if (!query) setCandidateSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setCandidateResolving(true);
      setCandidateResolveMessage(null);
      try {
        const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "instrument_resolve", arguments: { market: candidateMarket, query }, preserve_full_result: true });
        if (cancelled) return;
        const suggestions = instrumentSuggestions(resultEnvelope(response));
        setCandidateSuggestions(suggestions);
        setCandidateSuggestionsOpen(true);
        setCandidateResolveMessage(suggestions.length === 0 ? "No selectable Instrument matched this market and query." : null);
      } catch (cause) {
        if (cancelled) return;
        setCandidateSuggestions([]);
        setCandidateSuggestionsOpen(true);
        setCandidateResolveMessage(cause instanceof Error ? cause.message : "Instrument lookup failed.");
      } finally {
        if (!cancelled) setCandidateResolving(false);
      }
    }, 300);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [candidateInstrumentId, candidateInstrumentQuery, candidateMarket]);

  function goToSection(sectionId: string) {
    const section = document.getElementById(sectionId);
    if (!section) return;
    const top = window.scrollY + section.getBoundingClientRect().top - 22;
    window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    window.setTimeout(() => section.focus({ preventScroll: true }), 350);
  }

  function selectModule(module: ResearchModule) {
    setActiveModule(module);
    const url = new URL(window.location.href);
    url.searchParams.set("section", module);
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    const tabBar = document.querySelector(".research-section-nav");
    const top = tabBar ? window.scrollY + tabBar.getBoundingClientRect().top - 14 : window.scrollY;
    window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  }


  async function saveSubject() {
    setDetailError(null);
    const title = subjectDraft.title.trim();
    const summary = subjectDraft.summary.trim();
    if (!title || !summary) { setDetailError("Title and summary are required."); return; }
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

  function archiveSubject() {
    setArchiveConfirmation(true);
  }

  function confirmArchiveSubject() {
    setArchiveConfirmation(false);
    setArchiveReason(archiveReason || "Research scope ended or judgment invalidated");
    setArchiveReasonError(null);
    setArchiveReasonOpen(true);
  }

  async function submitArchiveSubject(reason: string) {
    const normalizedReason = reason.trim();
    if (!normalizedReason) {
      setArchiveReasonError("Archive Reason is required.");
      return;
    }
    setArchiveReasonOpen(false);
    try {
      await onWrite("investment_case_manage", { operation: "archive", case_id: text(researchSubject.subject_id), archived_reason: normalizedReason, reviewed_by: "user", idempotency_key: idempotencyKey("subject-archive") }, "investment_case_manage");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function executeRestoreSubject() {
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

  function restoreSubject() {
    setRestoreConfirmation(true);
  }

  async function activateSubject() {
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
    setDetailError(null);
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
    setDetailError(null);
    const invalidAssumption = thesisDraft.assumptions.find((item) => item.statement.trim() && (!item.basis.trim() || !item.falsifiability.trim()));
    const invalidInvalidation = thesisDraft.invalidations.find((item) => item.description.trim() && !item.observable.trim());
    if (invalidAssumption) { setDetailError("Every assumption needs Basis and Falsifiability."); return; }
    if (invalidInvalidation) { setDetailError("Every invalidation condition needs an Observable."); return; }
    const subjectStatus = String(researchSubject.status).toLowerCase();
    const changesToLiveStatus = ["active", "strengthened", "weakened"].includes(thesisDraft.thesisStatus) && (!thesisId || thesisStatusExplicit);
    if (subjectStatus === "draft" && changesToLiveStatus) { setDetailError("A Draft Research Subject cannot confirm a live Thesis. Activate the Research Subject first, or choose DRAFT status."); return; }
    if (thesisDraft.thesisRole === "sub" && !thesisDraft.parentThesisId) { setDetailError("A SUB Thesis must select a parent PRIMARY Thesis."); return; }
    const effectiveLive = thesisStatusExplicit || !thesisId ? LIVE_THESIS_STATUSES.has(thesisDraft.thesisStatus) : LIVE_THESIS_STATUSES.has(text(theses.find((item) => text(item.thesis_id) === thesisId)?.status, ""));
    const otherLivePrimary = theses.some((item) => text(item.thesis_id) !== thesisId && text(item.role) === "primary" && LIVE_THESIS_STATUSES.has(text(item.status)));
    if (thesisDraft.thesisRole === "primary" && effectiveLive && otherLivePrimary) { setDetailError("This Research Subject already has a live PRIMARY Thesis. Use SUB, COMPETITOR, or BEAR, or retire the existing PRIMARY first."); return; }
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
    if (!payload.title || !payload.statement || !payload.rationale || !payload.invalidation_check_note) { setDetailError("Thesis title, statement, rationale, and invalidation check note are required."); return; }
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
    await onWrite("research_judgment_confirm", request, "research_judgment_confirm");
    onRefresh();
  }

  async function proposeInstrumentCandidate() {
    setCandidateProposalError(null);
    setCandidateProposalSuccess(null);
    if (!candidateInstrumentId.trim() || !candidateDisplayName.trim()) {
      setCandidateProposalError("Choose an Instrument from the suggestions before creating a proposal.");
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
          ...(candidateThesisHint.trim() ? { thesis_hint: candidateThesisHint.trim() } : {}),
          triggers: [],
        },
        confirmation_mode: "strict_review",
        proposed_by: "user",
        proposed_by_rationale: "Instrument attachment proposed from the local Research workspace",
        idempotency_key: idempotencyKey("instrument-candidate-create"),
      }, "research_judgment_propose");
      setCandidateInstrumentQuery("");
      setCandidateInstrumentId("");
      setCandidateDisplayName("");
      setCandidateThesisHint("");
      setCandidateSuggestions([]);
      setCandidateSuggestionsOpen(false);
      setCandidateProposalSuccess("Instrument proposal created. Approve it in Pending Candidates to add it directly to Instruments.");
      onRefresh();
    } catch (cause) {
      setCandidateProposalError(cause instanceof Error ? cause.message : "Candidate proposal failed.");
    }
  }

  const tags = stringList(researchSubject.topic_tags);
  return (
    <div className="research-detail-stack">
      <HorizontalTabs className="research-section-nav" items={RESEARCH_MODULES.map((module) => ({ id: module.key, label: module.label, attention: module.key === "overview" && pendingCandidates.length > 0, suffix: module.key === "overview" && pendingCandidates.length > 0 ? <span className="horizontal-tab-count" aria-label={`${pendingCandidates.length} Pending Candidates`}>{pendingCandidates.length}</span> : undefined }))} value={activeModule} onChange={selectModule} ariaLabel="Research Subject Modules" idPrefix="research-tab" panelIdPrefix="research-panel" />
      <section id="research-panel-overview" className="research-module-panel" role="tabpanel" aria-labelledby="research-tab-overview" hidden={activeModule !== "overview"}>
      <Card id="research-section-overview" className="research-subject-detail" kicker={text(researchSubject.subject_type, "RESEARCH SUBJECT").replaceAll("_", " ").toUpperCase()} title={text(researchSubject.title, "Unnamed Research Subject")} action={<div className="research-detail-actions"><Badge value={text(researchSubject.status, "UNKNOWN").toUpperCase()} /><Link className="close-button" href={`/decision-workbench?subject_id=${encodeURIComponent(text(researchSubject.subject_id))}&capture=decision`}>Record Decision</Link><button className="close-button" type="button" onClick={() => { setDetailError(null); setSubjectEditor((value) => !value); }}>{subjectEditor ? "Close Editor" : "Edit Research Subject"}</button>{String(researchSubject.status).toLowerCase() === "draft" && <button className="close-button restore-text" type="button" disabled={busy} onClick={() => { void activateSubject(); }}>Start Tracking</button>}{String(researchSubject.status).toLowerCase() === "archived" ? <button className="close-button restore-text" type="button" disabled={busy} onClick={() => { void restoreSubject(); }}>Restore to Draft</button> : <button className="close-button warning-text" type="button" disabled={busy} onClick={archiveSubject}>Archive</button>}</div>}>
        <DescriptionList columns={3} items={[{ label: "Instruments", value: instrumentInventory.length === 0 ? "—" : <span className="research-overview-instruments">{instrumentInventory.map((instrument) => <span key={`${instrument.status}-${instrument.instrumentId}`}><strong>{instrument.displayName}</strong><small>{instrument.status}</small></span>)}</span> }, { label: "Created", value: formatDate(researchSubject.created_at) }, { label: "Updated", value: formatDate(researchSubject.updated_at) }]} />
        <p className="research-summary">{text(researchSubject.summary, "No Research Subject summary.")}</p>
        <div className="research-tags" aria-label="Research Subject Tags">{tags.length === 0 ? <span className="muted">No Tags</span> : tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
        {stringList(researchSubject.linked_subject_ids).length > 0 && <div className="research-linked-subjects"><span>Linked Research Subjects</span>{stringList(researchSubject.linked_subject_ids).map((subjectId) => <button type="button" key={subjectId} onClick={() => onSelectSubject(subjectId)}>{shortId(subjectId)}</button>)}</div>}
        {failure && <div className="research-state-error" role="status"><strong>Partial Read Failed</strong><span>{failure}</span><small>Research Subject metadata remains editable; fix state loading before editing Thesis.</small></div>}
      </Card>
      {subjectEditor && <SubjectEditor draft={subjectDraft} editing busy={busy} error={detailError} onChange={setSubjectDraft} onCancel={() => setSubjectEditor(false)} onSave={() => { void saveSubject(); }} />}
      <Card id="research-section-continuity" className="research-continuity-check" kicker="RESEARCH HEALTH" title="Health Check" action={continuitySignals.length > 0 ? <Badge value={`${continuitySignals.length} OPEN`} /> : undefined}>
        {continuitySignals.length === 0 ? <div className="attention-clear"><span aria-hidden="true">✓</span><div><strong>Core Judgment Controls Are Present</strong><small>Continue checking current facts and Catalyst outcomes separately.</small></div></div> : <div className="continuity-checklist">{continuitySignals.map((signal) => signal.key === "candidates" ? <button className="continuity-checklist-action" type="button" key={signal.key} onClick={() => goToSection("research-section-review")}><Badge value={signal.severity} /><div><strong>{signal.title}</strong><span>{signal.detail}</span></div><span className="continuity-action-copy">Open Queue <ArrowDown aria-hidden="true" /></span></button> : <article key={signal.key}><Badge value={signal.severity} /><div><strong>{signal.title}</strong><span>{signal.detail}</span></div></article>)}</div>}
      </Card>
      {pendingCandidates.length > 0 && <Card id="research-section-review" className="research-candidates-card" kicker="DECISION REQUIRED" title="Pending Candidates" description="Review the complete proposed change, then confirm, reject, or withdraw that exact Candidate." action={<Badge value={`${pendingCandidates.length} PROPOSED`} />}>{pendingCandidates.map((candidate) => <PendingCandidate key={text(candidate.candidate_id)} candidate={candidate} subjectStatus={String(researchSubject.status).toLowerCase()} busy={busy} onDecision={decideCandidate} />)}</Card>}
      <Disclosure className="research-raw" variant="code" title={<>View This Research Subject&apos;s Durable State</>}><pre>{displayJson(state)}</pre></Disclosure>
      </section>
      <section id="research-panel-instruments" className="research-module-panel" role="tabpanel" aria-labelledby="research-tab-instruments" hidden={activeModule !== "instruments"}>
      <Card id="research-section-selection" className="research-selection-card" kicker="INSTRUMENT SELECTION" title="Instruments" action={<Badge value={`${instrumentInventory.length} INSTRUMENTS`} />}>
        <div className="research-selection-subhead"><strong>Propose Instrument</strong></div>
        <div className="research-selection-create">
          <Field label="Market" required><select value={candidateMarket} required onChange={(event) => { setCandidateMarket(event.target.value); setCandidateInstrumentQuery(""); setCandidateInstrumentId(""); setCandidateDisplayName(""); setCandidateSuggestions([]); setCandidateResolveMessage(null); }}>{CANDIDATE_MARKETS.map((market) => <option key={market}>{market}</option>)}</select></Field>
          <div className="research-field research-instrument-combobox">
            <label htmlFor="candidate-instrument-query"><span><b className="required-mark" aria-hidden="true">*</b>Instrument ID</span></label>
            <div className={`research-combobox-control${candidateInstrumentId ? " selected" : ""}`}><Search aria-hidden="true" /><input id="candidate-instrument-query" role="combobox" aria-autocomplete="list" aria-expanded={candidateSuggestionsOpen} aria-controls="candidate-instrument-suggestions" required autoComplete="off" value={candidateInstrumentQuery} onFocus={() => { if (candidateSuggestions.length > 0 || candidateResolveMessage) setCandidateSuggestionsOpen(true); }} onBlur={() => window.setTimeout(() => setCandidateSuggestionsOpen(false), 120)} onChange={(event) => { setCandidateInstrumentQuery(event.target.value); setCandidateInstrumentId(""); setCandidateDisplayName(""); setCandidateSuggestionsOpen(true); setCandidateProposalError(null); }} placeholder="Search symbol or name, e.g. UCO" />{candidateResolving && <span className="research-combobox-loading">Searching…</span>}</div>
            {candidateSuggestionsOpen && (candidateSuggestions.length > 0 || candidateResolveMessage) && <div className="research-combobox-options" id="candidate-instrument-suggestions" role="listbox">{candidateSuggestions.map((suggestion) => <button type="button" role="option" aria-selected={candidateInstrumentId === suggestion.instrument_id} key={suggestion.instrument_id} onMouseDown={(event) => event.preventDefault()} onClick={() => { setCandidateInstrumentId(suggestion.instrument_id); setCandidateInstrumentQuery(suggestion.instrument_id); setCandidateDisplayName(suggestion.name); setCandidateSuggestionsOpen(false); setCandidateResolveMessage(null); setCandidateProposalError(null); }}><strong>{suggestion.symbol} · {suggestion.name}</strong><small>{suggestion.instrument_id}{suggestion.exchange ? ` · ${suggestion.exchange}` : ""}</small></button>)}{candidateSuggestions.length === 0 && candidateResolveMessage && <p role="status">{candidateResolveMessage}</p>}</div>}
          </div>
          <Field label="Display Name" required><input value={candidateDisplayName} required readOnly placeholder="Filled after Instrument selection" /></Field>
          <Field label="Reason"><input value={candidateThesisHint} onChange={(event) => setCandidateThesisHint(event.target.value)} placeholder="Optional" /></Field>
          <ActionButton onClick={() => { void proposeInstrumentCandidate(); }} busy={busy || candidateResolving} disabled={!candidateInstrumentId || !candidateDisplayName}>Propose Instrument</ActionButton>
        </div>
        {candidateProposalError && <div className="inline-error research-selection-feedback" role="alert">{candidateProposalError}</div>}
        {candidateProposalSuccess && <div className="inline-success research-selection-feedback" role="status">{candidateProposalSuccess}</div>}
        <div className="research-selection-subhead"><strong>Current Instruments</strong></div>
        {instrumentInventory.length === 0 ? <Empty>No Instruments are attached to this Research Subject.</Empty> : <div className="research-selection-list">{primaryInstrumentId && <article className="research-selection-item status-primary"><div><strong>{shortId(primaryInstrumentId)}</strong><small>{primaryInstrumentId}</small></div><div className="research-selection-actions"><Badge value="PRIMARY" /></div></article>}{additionalInstrumentCandidates.map((candidate) => <article className="research-selection-item" key={text(candidate.item_id)}><div><strong>{text(candidate.display_name)}</strong><small>{text(candidate.instrument_id, `${text(candidate.market)}:${text(candidate.symbol)}`)}</small>{text(candidate.thesis_hint, "") ? <p>{text(candidate.thesis_hint)}</p> : null}</div><div className="research-selection-actions"><Badge value="INSTRUMENT" /></div></article>)}</div>}
      </Card>
      </section>
      <section id="research-panel-thesis" className="research-module-panel" role="tabpanel" aria-labelledby="research-tab-thesis" hidden={activeModule !== "thesis"}>
      <Card id="research-section-thesis" className="research-theses-card" kicker="CURRENT JUDGMENT" title="Thesis" description="Versioned, falsifiable research judgments." action={!thesisEditor ? <ActionButton onClick={() => startThesisEditor(undefined, true)}>Create Thesis</ActionButton> : <Badge value="EDITING" />}>
        {thesisEditor ? <ThesisEditor draft={thesisDraft} thesisId={thesisId} availableTheses={theses} statusExplicit={thesisStatusExplicit} subjectStatus={String(researchSubject.status).toLowerCase()} busy={busy} error={detailError} onChange={setThesisDraft} onStatusExplicitChange={setThesisStatusExplicit} onCancel={() => setThesisEditor(false)} onSave={() => { void saveThesis(); }} /> : theses.length === 0 ? <div className="research-no-thesis"><p>This Research Subject has no Thesis yet. Create a new Thesis candidate; Draft Research Subjects default to DRAFT Thesis.</p><ActionButton onClick={() => startThesisEditor(undefined, true)}>Create Thesis</ActionButton></div> : <ThesisRelationshipList theses={theses} revisions={revisions} assumptions={assumptions} invalidations={invalidations} onEdit={startThesisEditor} />}
      </Card>
      <Card className="research-context-card" kicker="OPEN ENDS" title="Judgment Context" description="Questions that remain unresolved."><div className="research-context-grid research-context-single"><div><span>Open Questions · {openQuestions.length}</span>{openQuestions.length === 0 ? <p className="muted">No open questions.</p> : <ul>{openQuestions.map((question) => <li key={text(question.question_id)}>{text(question.text)}</li>)}</ul>}</div></div></Card>
      </section>
      <ResearchContinuity activeModule={activeModule} subject={researchSubject} state={state} onWrite={onWrite} onRefresh={onRefresh} busy={busy} />
      <section id="research-panel-monitors" className="research-module-panel" role="tabpanel" aria-labelledby="research-tab-monitors" hidden={activeModule !== "monitors"}>
      <MonitorLinks monitorData={monitorData} loading={monitorLoading} subjectId={text(researchSubject.subject_id)} />
      </section>
      <ConfirmationDialog
        open={archiveConfirmation}
        title="Archive Research Subject"
        description="Archive this Research Subject? Archiving does not delete historical Theses, evidence, or Monitors. An archive reason is required and the lifecycle gate remains explicit."
        confirmLabel="Continue to Archive Reason"
        tone="warning"
        onCancel={() => setArchiveConfirmation(false)}
        onConfirm={confirmArchiveSubject}
      />
      <TextInputDialog
        open={archiveReasonOpen}
        title="Archive Research Subject"
        description="Record why this Research Subject is no longer in scope. This note becomes part of the durable archive audit record."
        label="Archive Reason"
        value={archiveReason}
        onChange={(value) => { setArchiveReason(value); setArchiveReasonError(null); }}
        onSubmit={(value) => { void submitArchiveSubject(value); }}
        onCancel={() => setArchiveReasonOpen(false)}
        required
        multiline
        helperText="Required. Historical Theses, evidence, Monitors, and audit records are preserved."
        error={archiveReasonError}
        confirmLabel="Archive Research Subject"
        tone="warning"
        busy={busy}
      />
      <ConfirmationDialog
        open={restoreConfirmation}
        title="Restore Research Subject"
        description="Restore this archived Research Subject to Draft? Historical Theses, Trade Plans, and audit records will be preserved. A new candidate will be proposed and explicitly confirmed."
        confirmLabel="Restore to Draft"
        onCancel={() => setRestoreConfirmation(false)}
        onConfirm={() => { setRestoreConfirmation(false); void executeRestoreSubject(); }}
        busy={busy}
      />
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
  const [subjectEditorError, setSubjectEditorError] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("create") !== "observation") return;
    const instrumentId = url.searchParams.get("instrument_id")?.trim() ?? "";
    if (!instrumentId) return;
    const sourceTitle = url.searchParams.get("title")?.trim() || shortId(instrumentId);
    const symbol = shortId(instrumentId);
    setSubjectDraft({
      subjectType: instrumentId.startsWith("equity:") ? "company" : "theme",
      title: `${sourceTitle} Research`,
      summary: `Research ${symbol} fundamentals, catalysts, valuation, market structure, and evolving external observations.`,
      instrument: instrumentId,
      tags: `${symbol.toLowerCase()}, observation_source`,
      linkedSubjectIds: "",
    });
    setSubjectEditorError(null);
    setSubjectEditor(true);
    url.searchParams.delete("create");
    url.searchParams.delete("instrument_id");
    url.searchParams.delete("title");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, []);
  useAgentPageContext({ surface: "research", selected_subject_id: selectedSubjectId });
  const items = listOf<SubjectAggregate>(result.data, "subjects");
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filtered = useMemo(() => items.filter((item) => { const researchSubject = item.subject ?? {}; const matchesStatus = status === "ALL" || text(researchSubject.status).toUpperCase() === status; return matchesStatus && (!normalizedQuery || subjectSearchText(item).includes(normalizedQuery)); }), [items, normalizedQuery, status]);
  const selected = items.find((item) => String(item.subject?.subject_id) === selectedSubjectId) ?? null;

  function selectSubject(subjectId: string) {
    setSelectedSubjectId(subjectId);
    const url = new URL(window.location.href);
    url.hash = `subject-${subjectId}`;
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }

  function clearSubjectFilters() {
    setQuery("");
    setStatus("ALL");
  }

  async function write(toolName: string, request: Dict, confirmation?: string): Promise<Dict> {
    setWriting(true);
    setWriteError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: toolName, arguments: { request }, confirmation });
      const envelope = assertEnvelopeSuccess(response);
      notifyConsole({ title: "Research Updated", message: "Refreshing durable Research state.", tone: "success" });
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
    setSubjectEditorError(null);
    const title = subjectDraft.title.trim();
    const summary = subjectDraft.summary.trim();
    if (!title || !summary) { setSubjectEditorError("Title and summary are required."); return; }
    if ((subjectDraft.subjectType === "company" || subjectDraft.subjectType === "catalyst") && !subjectDraft.instrument.trim()) { setSubjectEditorError("Primary Instrument ID is required for this Research Subject Type."); return; }
    try {
      const response = await write("investment_case_manage", { operation: "create", case_type: subjectDraft.subjectType, title, summary, primary_instrument_id: subjectDraft.instrument.trim() || null, topic_tags: splitList(subjectDraft.tags), linked_case_ids: splitList(subjectDraft.linkedSubjectIds), confirmed_by: "user", idempotency_key: idempotencyKey("subject-create") }, "investment_case_manage");
      const created = envelopeData<Dict>(response);
      const createdId = created?.case_id;
      if (typeof createdId === "string") setSelectedSubjectId(createdId);
      setSubjectEditor(false);
      setSubjectEditorError(null);
      setSubjectDraft(EMPTY_SUBJECT_DRAFT);
      result.refresh();
    } catch { /* write() keeps the local error visible */ }
  }

  return (
    <ConsoleShell active="research" pageActions={<PageActionMenu ariaLabel="Research Page Actions" items={[
      { id: "create", label: subjectEditor ? "Close Create" : "Create Research Subject", description: "Open the Research Subject editor", icon: <Plus aria-hidden="true" />, onSelect: () => { setSubjectDraft(EMPTY_SUBJECT_DRAFT); setSubjectEditorError(null); setSubjectEditor((value) => !value); } },
      { id: "refresh", label: result.loading ? "Refreshing…" : "Refresh", description: "Reload durable Research data", icon: <RefreshCw aria-hidden="true" className={result.loading ? "spin" : undefined} />, disabled: result.loading, onSelect: result.refresh },
    ]} />}>
      <DataBoundary loading={result.loading} error={result.error}>
        <div className="research-page">
          <div className="research-master-detail">
            <aside className="research-index" aria-label="Research Library">
              <Card className="research-index-card">
                {writeError && <div className="inline-error research-library-feedback" role="alert">{writeError}</div>}
                <EntityBrowser
                  items={items}
                  filteredItems={filtered}
                  selectedId={selectedSubjectId}
                  getId={(item) => String(item.subject?.subject_id ?? "")}
                  onSelect={selectSubject}
                  onClearSelection={() => setSelectedSubjectId(null)}
                  hashToId={(hash) => hash.match(/^#subject-(case_.+)$/)?.[1] ?? hash.match(/^#case-(case_.+)$/)?.[1] ?? null}
                  search={{ value: query, onChange: setQuery, label: "Text Filter", placeholder: "Title, instrument, tags, Thesis", ariaLabel: "Filter Research Subjects" }}
                  status={{ value: status, onChange: setStatus, label: "Status", ariaLabel: "Filter by Research Subject Status", options: [{ value: "ALL", label: "All (Including Archived)" }, ...SUBJECT_STATUSES.map((value) => ({ value: value.toUpperCase(), label: optionLabel(value) }))] }}
                  onClearFilters={clearSubjectFilters}
                  clearDisabled={!query && status === "ALL"}
                  filteredNotice={<div className="entity-filter-notice" role="status"><span>The current Research Subject is outside this filter.</span><button type="button" onClick={clearSubjectFilters}>Show Current</button></div>}
                  resultLabel={(count) => <><strong>{count}</strong> {count === 1 ? "Subject" : "Subjects"}</>}
                  emptyMessage={<Empty>No durable Research Subjects.</Empty>}
                  noMatchesMessage={<Empty>No Research Subjects match the current filters.</Empty>}
                  listAriaLabel="Filtered Research Subjects"
                  previousAriaLabel="Show Previous Research Subjects"
                  nextAriaLabel="Show Next Research Subjects"
                  rangeAriaLabel={(start, end, total) => `Showing Research Subjects ${start} through ${end} of ${total}`}
                  responsivePageSize={RESEARCH_PAGE_SIZE}
                  showRange={false}
                  hashKey="subject"
                  renderItem={(item, isSelected, select) => {
                    const researchSubject = item.subject ?? {};
                    const subjectId = String(researchSubject.subject_id ?? "");
                    const state = stateData(item) ?? {};
                    const thesisCount = listOf<Dict>(state, "theses").length;
                    return <button type="button" role="option" aria-selected={isSelected} id={`research-subject-${subjectId}`} className={`research-index-item ${isSelected ? "selected" : ""}`} onClick={() => select(subjectId)} key={subjectId}><span className="research-index-status"><span className={`research-status-dot status-${text(researchSubject.status, "unknown").toLowerCase()}`} aria-hidden="true" />{text(researchSubject.status, "UNKNOWN")}</span><strong>{text(researchSubject.title, "Unnamed Research Subject")}</strong><small>{shortId(researchSubject.primary_instrument_id)} · {thesisCount} Thesis</small><time>{formatDate(researchSubject.updated_at)}</time></button>;
                  }}
                />
              </Card>
            </aside>
            {subjectEditor && <SubjectEditor draft={subjectDraft} editing={false} busy={writing} error={subjectEditorError} onChange={setSubjectDraft} onCancel={() => setSubjectEditor(false)} onSave={() => { void createSubject(); }} />}
            <main className="research-detail" aria-live="polite">{selected ? <ResearchSubjectDetail item={selected} monitorData={monitorResult.data} monitorLoading={monitorResult.loading} onSelectSubject={selectSubject} onRefresh={result.refresh} onWrite={write} busy={writing} /> : <Empty>Select a Research Subject above.</Empty>}</main>
          </div>
          {monitorResult.error && <div className="inline-error">Failed to read linked Monitors: {monitorResult.error}. The Research Subject remains available for viewing and editing.</div>}
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
