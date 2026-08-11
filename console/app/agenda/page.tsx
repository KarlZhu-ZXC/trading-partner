"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ActionButton,
  Badge,
  Card,
  DataBoundary,
  Empty,
  RefreshButton,
  formatDate,
  shortId,
} from "../components/ui";
import { ConsoleShell } from "../components/console-shell";
import { API_BASE, envelopeData, listOf, postApi, useApi } from "../lib/api";

type Dict = Record<string, unknown>;
type AgendaAction = "CREATE" | "REVISE" | "CANCEL" | "LINK_OUTCOME";

type AgendaSyncProviderResult = {
  vendor: string;
  scope_ref: string;
  status: string;
  candidate_count: number;
  error_code: string | null;
  warning_codes: string[];
};

type AgendaSyncReceipt = {
  receipt_id: string;
  status: string;
  as_of: string;
  window_start: string;
  window_end: string;
  scope_count: number;
  eligible_instrument_count: number;
  succeeded_scope_count: number;
  failed_scope_count: number;
  candidate_count: number;
  appended_count: number;
  revised_count: number;
  date_drift_count: number;
  unchanged_count: number;
  provider_results: AgendaSyncProviderResult[];
  limitation_codes: string[];
  started_at: string;
  completed_at: string;
  schema_version: number;
  execution_effect: boolean;
};

type AgendaItemForm = {
  agenda_item_id: string;
  expected_version: string;
  event_id: string;
  report_id: string;
  evidence_id: string;
  outcome_occurred_at: string;
  outcome_note: string;
  subject_id: string;
  instrument_id: string;
  kind: string;
  title: string;
  fiscal_period: string;
  timezone: string;
  date_certainty: string;
  window_start: string;
  window_end: string;
  expected_question: string;
  source_reference: string;
  revision_note: string;
  cancellation_reason: string;
};

type AgendaGroup = {
  agenda_item_id: string;
  versions: Dict[];
  latest: Dict;
  scope: string;
  currentBucket: AgendaBucket;
};

type AgendaBucket = "ALL" | "FUTURE" | "UPCOMING_7D" | "OVERDUE" | "ACTIVE" | "ARCHIVED";

const TIME_FILTERS = ["ALL", "FUTURE", "UPCOMING_7D", "OVERDUE", "ACTIVE", "ARCHIVED"] as const;
const KIND_OPTIONS = [
  "USER_DEFINED",
  "EARNINGS",
  "FILING",
  "DIVIDEND",
  "CORPORATE_ACTION",
  "INVESTOR_EVENT",
  "MACRO_RELEASE",
  "POLICY",
  "INDUSTRY",
] as const;
const SCOPE_OPTIONS = ["PORTFOLIO", "WATCHLIST", "SUBJECT", "EXPLICIT", "GLOBAL", "ALL"] as const;
const DATE_CERTAINTY_OPTIONS = ["CONFIRMED", "ESTIMATED", "RANGE", "UNKNOWN"] as const;
const TIMEZONE_OPTIONS = [
  "UTC",
  "America/New_York",
  "Asia/Shanghai",
  "Asia/Kolkata",
  "Europe/London",
] as const;

const EMPTY_FORM: AgendaItemForm = {
  agenda_item_id: "",
  expected_version: "1",
  event_id: "",
  report_id: "",
  evidence_id: "",
  outcome_occurred_at: "",
  outcome_note: "",
  subject_id: "",
  instrument_id: "",
  kind: "USER_DEFINED",
  title: "",
  fiscal_period: "",
  timezone: "UTC",
  date_certainty: "UNKNOWN",
  window_start: "",
  window_end: "",
  expected_question: "",
  source_reference: "",
  revision_note: "",
  cancellation_reason: "",
};

function asDict(value: unknown): Dict {
  return value !== null && typeof value === "object" ? (value as Dict) : {};
}

function text(value: unknown, fallback = "—"): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : fallback;
}

function splitIdentifierList(value: string): string[] {
  return value
    .split(/[,\n]+/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function asInt(value: unknown, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.trunc(parsed);
}

function toNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function toDate(value: unknown): number {
  const raw = text(value, "");
  if (!raw) return 0;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? 0 : parsed.getTime();
}

function toInputDate(value: unknown): string {
  const raw = text(value, "");
  if (!raw || raw === "—") return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "";
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function fromInputDate(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const parsed = new Date(trimmed);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toISOString();
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0);
}

function asSingleStringList(value: unknown): string[] {
  const candidate = text(value, "");
  return candidate ? [candidate] : [];
}

function unwrap(payload: unknown): Dict {
  const source = asDict(payload);
  const data = envelopeData<Dict>(source);
  if (data && Object.keys(data).length > 0) return data;
  return source;
}

function unwrapAgendaSync(payload: unknown): AgendaSyncReceipt | null {
  const source = asDict(payload);
  const data = asDict(source.data);
  const result = asDict(source.result);

  const receipt: AgendaSyncReceipt = {
    receipt_id: text(source.receipt_id, text(data.receipt_id, text(result.receipt_id))),
    status: text(source.status, text(data.status, text(result.status))),
    as_of: text(source.as_of, text(data.as_of, text(result.as_of))),
    window_start: text(source.window_start, text(data.window_start, text(result.window_start))),
    window_end: text(source.window_end, text(data.window_end, text(result.window_end))),
    scope_count: asInt(
      source.scope_count ?? data.scope_count ?? result.scope_count,
      0,
    ),
    eligible_instrument_count: asInt(
      source.eligible_instrument_count ?? data.eligible_instrument_count ?? result.eligible_instrument_count,
      0,
    ),
    succeeded_scope_count: asInt(
      source.succeeded_scope_count ?? data.succeeded_scope_count ?? result.succeeded_scope_count,
      0,
    ),
    failed_scope_count: asInt(
      source.failed_scope_count ?? data.failed_scope_count ?? result.failed_scope_count,
      0,
    ),
    candidate_count: asInt(
      source.candidate_count ?? data.candidate_count ?? result.candidate_count,
      0,
    ),
    appended_count: asInt(
      source.appended_count ?? data.appended_count ?? result.appended_count,
      0,
    ),
    revised_count: asInt(
      source.revised_count ?? data.revised_count ?? result.revised_count,
      0,
    ),
    date_drift_count: asInt(
      source.date_drift_count ?? data.date_drift_count ?? result.date_drift_count,
      0,
    ),
    unchanged_count: asInt(
      source.unchanged_count ?? data.unchanged_count ?? result.unchanged_count,
      0,
    ),
    provider_results: listOf<AgendaSyncProviderResult>(
      source,
      "provider_results",
    ),
    limitation_codes: listOf<string>(
      source.limitation_codes ?? data.limitation_codes ?? result.limitation_codes,
      "a",
    ),
    started_at: text(source.started_at, text(data.started_at, text(result.started_at))),
    completed_at: text(source.completed_at, text(data.completed_at, text(result.completed_at))),
    schema_version: asInt(
      source.schema_version ?? data.schema_version ?? result.schema_version,
      0,
    ),
    execution_effect: Boolean(
      source.execution_effect ?? data.execution_effect ?? result.execution_effect,
    ),
  };

  if (!receipt.receipt_id) return null;
  return receipt;
}

function extractItems(payload: unknown): Dict[] {
  const source = unwrap(payload);
  const agendaEnvelope = asDict(source.agenda);
  const candidates = [
    source,
    asDict(source.data),
    agendaEnvelope,
    unwrap(agendaEnvelope),
    asDict(agendaEnvelope.data),
    asDict(source.result),
    asDict(source.data).agenda,
    asDict(asDict(source.result).agenda),
  ];
  const rows: Dict[] = [];
  for (const candidate of candidates) {
    rows.push(...listOf<Dict>(candidate, "items"));
  }
  const dedup = new Map<string, Dict>();
  for (const item of rows) {
    const itemId = text(item.agenda_item_id, "");
    if (!itemId) continue;
    const version = toNumber(item.version);
    const key = `${itemId}#${version}`;
    if (!dedup.has(key)) dedup.set(key, item);
  }
  return Array.from(dedup.values());
}

function extractSubjects(payload: unknown): Dict[] {
  const source = asDict(payload);
  const subjectCandidates = [
    source.subjects,
    asDict(source.subjects).data,
    asDict(source.subjects).result,
  ];
  const rows: Dict[] = [];
  for (const candidate of subjectCandidates) {
    rows.push(...listOf<Dict>(candidate, "items"));
  }
  const dedup = new Map<string, Dict>();
  for (const item of rows) {
    const id = text(item.case_id) || text(item.subject_id);
    if (!id || dedup.has(id)) continue;
    dedup.set(id, item);
  }
  return Array.from(dedup.values());
}

function scopeLabel(item: Dict): string {
  const scopes = asStringList(item.scope_reasons);
  if (scopes.length > 0) return scopes[0];
  if (text(item.subject_id, "") !== "—") return "SUBJECT";
  if (text(item.instrument_id, "") !== "—") return "INSTRUMENT";
  return "GLOBAL";
}

function classify(item: Dict, nowMs: number): { bucket: AgendaBucket; badge: string } {
  const status = text(item.status, "UPCOMING");
  if (["CANCELLED", "OCCURRED", "SUPERSEDED"].includes(status)) {
    return { bucket: "ARCHIVED", badge: status };
  }

  const start = toDate(item.window_start);
  const end = toDate(item.window_end);
  if (status === "UPCOMING") {
    if (start > nowMs) {
      const threshold = nowMs + 7 * 24 * 60 * 60 * 1000;
      return { bucket: start <= threshold ? "UPCOMING_7D" : "FUTURE", badge: start <= threshold ? "UPCOMING 7D" : "FUTURE" };
    }
    if (end > 0 && end < nowMs) {
      return { bucket: "OVERDUE", badge: "OVERDUE" };
    }
    return { bucket: "ACTIVE", badge: "ACTIVE" };
  }
  if (status === "ACTIVE") return { bucket: "ACTIVE", badge: "ACTIVE" };
  return { bucket: "ALL", badge: status };
}

function groupByAgendaId(payload: Dict, nowMs: number): AgendaGroup[] {
  const items = extractItems(payload);
  const grouped = new Map<string, Dict[]>();
  for (const item of items) {
    const id = text(item.agenda_item_id, "");
    if (!id) continue;
    const versions = grouped.get(id) ?? [];
    versions.push(item);
    grouped.set(id, versions);
  }

  const rows: AgendaGroup[] = [];
  for (const [agendaItemId, versions] of grouped) {
    const sorted = [...versions].sort((left, right) => {
      const leftVersion = toNumber(right.version) - toNumber(left.version);
      if (leftVersion !== 0) return leftVersion;
      return toDate(right.recorded_at) - toDate(left.recorded_at);
    });
    const latest = sorted[0] ?? {};
    const classification = classify(latest, nowMs);
    rows.push({
      agenda_item_id: agendaItemId,
      versions: sorted,
      latest,
      scope: scopeLabel(latest),
      currentBucket: classification.bucket,
    });
  }

  return rows.sort((left, right) => {
    const leftStart = toDate(left.latest.window_start);
    const rightStart = toDate(right.latest.window_start);
    if (leftStart !== rightStart) return rightStart - leftStart;
    return text(left.latest.agenda_item_id).localeCompare(text(right.latest.agenda_item_id));
  });
}

function parseSummary(payload: unknown): { upcoming7d: number; upcoming: number; overdue: number; coverageGap: number } {
  const source = unwrap(payload);
  const rows = groupByAgendaId(source, Date.now());
  const nowMs = Date.now();
  let upcoming7d = 0;
  let upcoming = 0;
  let overdue = 0;
  for (const row of rows) {
    const classification = classify(row.latest, nowMs);
    if (classification.badge === "UPCOMING 7D" || classification.badge === "FUTURE") {
      upcoming += 1;
    }
    if (classification.badge === "UPCOMING 7D") {
      upcoming7d += 1;
    }
    if (classification.badge === "OVERDUE") {
      overdue += 1;
    }
  }

  const coveragePayload = asDict(asDict(source).coverage);
  const coverage = listOf<Dict>(source, "coverage");
  const coverageGap = coverage.length > 0
    ? coverage.filter((entry) => text(entry.status) === "UNAVAILABLE").length
    : 0;
  const explicitCoverageGap = toNumber(coveragePayload.coverage_gap_count);
  return {
    upcoming7d,
    upcoming,
    overdue,
    coverageGap: explicitCoverageGap || coverageGap,
  };
}

function buildRequest(action: AgendaAction, form: AgendaItemForm): Dict {
  const request: Dict = {
    operation: "agenda_item",
    action,
    confirmed_by: "user",
    authorization_note: `Agenda ${action.toLowerCase()} submitted from local console at ${new Date().toISOString()}`,
    idempotency_key: `agenda-${action.toLowerCase()}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
    payload: {},
  };

  if (action !== "CREATE") {
    request.agenda_item_id = form.agenda_item_id.trim();
    request.expected_version = toNumber(form.expected_version) || 1;
  }

  if (action === "CANCEL") {
    request.payload = {
      cancellation_reason: form.cancellation_reason.trim(),
    };
    return request;
  }
  if (action === "LINK_OUTCOME") {
    const payload: Dict = {};
    if (form.event_id.trim()) payload.event_id = form.event_id.trim();
    if (form.report_id.trim()) payload.report_id = form.report_id.trim();
    if (form.evidence_id.trim()) payload.evidence_id = form.evidence_id.trim();
    if (form.outcome_occurred_at.trim()) {
      payload.outcome_occurred_at = fromInputDate(form.outcome_occurred_at);
    }
    payload.outcome_note = form.outcome_note.trim();
    request.payload = payload;
    return request;
  }

  const payload: Dict = {
    kind: form.kind,
    title: form.title.trim(),
    timezone: form.timezone,
    date_certainty: form.date_certainty,
  };

  if (form.fiscal_period.trim()) {
    payload.fiscal_period = form.fiscal_period.trim();
  }
  if (form.subject_id.trim()) {
    payload.subject_id = form.subject_id.trim();
  }
  if (form.instrument_id.trim()) {
    payload.instrument_id = form.instrument_id.trim();
  }
  if (form.source_reference.trim()) {
    payload.source_reference = form.source_reference.trim();
  }
  if (form.expected_question.trim()) {
    payload.expected_question = form.expected_question.trim();
  }
  if (form.revision_note.trim()) {
    payload.revision_note = form.revision_note.trim();
  }
  if (form.date_certainty !== "UNKNOWN") {
    payload.window_start = fromInputDate(form.window_start);
    payload.window_end = fromInputDate(form.window_end);
  }

  request.payload = payload;
  return request;
}

export default function CatalystAgendaPage() {
  const [agendaOffset, setAgendaOffset] = useState(0);
  const agendaApi = useApi<Dict>(`/api/agenda?window_days=60&limit=200&offset=${agendaOffset}`);
  const [busy, setBusy] = useState<AgendaAction | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const [summaryBusy, setSummaryBusy] = useState<"preview" | "send" | null>(null);
  const [action, setAction] = useState<AgendaAction>("CREATE");
  const [formVisible, setFormVisible] = useState(false);
  const [form, setForm] = useState<AgendaItemForm>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [summaryMessage, setSummaryMessage] = useState<string | null>(null);
  const [summaryPreview, setSummaryPreview] = useState<string | null>(null);
  const [summarySendResult, setSummarySendResult] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [syncPayload, setSyncPayload] = useState({
    windowDays: "30",
    instrumentIds: "",
    fredReleaseIds: "",
  });
  const [lastSync, setLastSync] = useState<AgendaSyncReceipt | null>(null);
  const [historyById, setHistoryById] = useState<Record<string, Dict[]>>({});
  const [historyLoading, setHistoryLoading] = useState<string | null>(null);

  const [timeFilter, setTimeFilter] = useState<(typeof TIME_FILTERS)[number]>("ALL");
  const [kindFilter, setKindFilter] = useState("ALL");
  const [scopeFilter, setScopeFilter] = useState<(typeof SCOPE_OPTIONS)[number]>("ALL");
  const [statusFilter, setStatusFilter] = useState("ALL");

  const nowMs = Date.now();
  const agendaPayload = unwrap(agendaApi.data);
  const agendaPage = unwrap(asDict(agendaApi.data).agenda);
  const agendaHasMore = agendaPage.has_more === true;
  const agendaTotal = toNumber(agendaPage.total);
  const grouped = useMemo(() => groupByAgendaId(agendaPayload, nowMs), [agendaPayload, nowMs]);
  const summary = useMemo(() => parseSummary(agendaPayload), [agendaPayload]);
  const subjects = useMemo(() => extractSubjects(agendaApi.data), [agendaApi.data]);
  const candidateQuery = new URLSearchParams({ limit: action === "LINK_OUTCOME" ? "50" : "1" });
  if (action === "LINK_OUTCOME" && form.subject_id.trim()) {
    candidateQuery.set("subject_id", form.subject_id.trim());
  } else if (action === "LINK_OUTCOME" && form.instrument_id.trim()) {
    candidateQuery.set("instrument_id", form.instrument_id.trim());
  }
  const candidateApi = useApi<Dict>(`/api/agenda/outcome-candidates?${candidateQuery.toString()}`);
  const outcomeCandidates = useMemo(
    () => listOf<Dict>(unwrap(asDict(candidateApi.data).candidates), "items")
      .filter((item) => ["event", "report", "evidence"].includes(text(item.entity_type, "").toLowerCase())),
    [candidateApi.data],
  );

  const kindOptions = useMemo(() => {
    const dynamic = grouped.map((item) => text(item.latest.kind, "").toUpperCase()).filter(Boolean);
    return ["ALL", ...new Set([...KIND_OPTIONS, ...dynamic])];
  }, [grouped]);

  const scopeOptions = useMemo(() => {
    const dynamic = grouped.map((item) => item.scope).filter(Boolean);
    return ["ALL", ...new Set([...SCOPE_OPTIONS.filter((item) => item !== "ALL"), ...dynamic])];
  }, [grouped]);

  const statusOptions = useMemo(() => {
    const dynamic = grouped.map((item) => text(item.latest.status, "")).filter(Boolean);
    return ["ALL", ...new Set(dynamic)];
  }, [grouped]);

  const filtered = useMemo(() => {
    return grouped.filter((group) => {
      const classification = classify(group.latest, nowMs);
      if (timeFilter !== "ALL" && classification.bucket !== timeFilter) return false;
      if (kindFilter !== "ALL" && text(group.latest.kind, "").toUpperCase() !== kindFilter) return false;
      if (scopeFilter !== "ALL" && group.scope !== scopeFilter) return false;
      if (statusFilter !== "ALL" && text(group.latest.status, "") !== statusFilter) return false;
      return true;
    });
  }, [grouped, timeFilter, kindFilter, scopeFilter, statusFilter, nowMs]);

  function beginCreate() {
    setAction("CREATE");
    setForm(EMPTY_FORM);
    setFormVisible(true);
    setError(null);
    setMessage(null);
  }

  function beginRevise(group: AgendaGroup) {
    const item = group.latest;
    setAction("REVISE");
    setForm({
      agenda_item_id: text(item.agenda_item_id),
      expected_version: String(toNumber(item.version) || 1),
      event_id: "",
      report_id: "",
      evidence_id: "",
      outcome_occurred_at: "",
      outcome_note: "",
      subject_id: text(item.subject_id, ""),
      instrument_id: text(item.instrument_id, ""),
      kind: text(item.kind, "USER_DEFINED"),
      title: text(item.title),
      fiscal_period: text(item.fiscal_period, ""),
      timezone: text(item.timezone, "UTC"),
      date_certainty: text(item.date_certainty, "UNKNOWN"),
      window_start: toInputDate(item.window_start),
      window_end: toInputDate(item.window_end),
      expected_question: text(item.expected_question, ""),
      source_reference: text(item.source_reference, ""),
      revision_note: text(item.revision_note, ""),
      cancellation_reason: "",
    });
    setFormVisible(true);
    setError(null);
    setMessage(null);
  }

  function beginCancel(group: AgendaGroup) {
    const item = group.latest;
    setAction("CANCEL");
    setForm({
      agenda_item_id: text(item.agenda_item_id),
      expected_version: String(toNumber(item.version) || 1),
      event_id: "",
      report_id: "",
      evidence_id: "",
      outcome_occurred_at: "",
      outcome_note: "",
      subject_id: text(item.subject_id, ""),
      instrument_id: text(item.instrument_id, ""),
      kind: text(item.kind, "USER_DEFINED"),
      title: text(item.title),
      fiscal_period: text(item.fiscal_period, ""),
      timezone: text(item.timezone, "UTC"),
      date_certainty: text(item.date_certainty, "UNKNOWN"),
      window_start: "",
      window_end: "",
      expected_question: text(item.expected_question, ""),
      source_reference: text(item.source_reference, ""),
      revision_note: text(item.revision_note, ""),
      cancellation_reason: "",
    });
    setFormVisible(true);
    setError(null);
    setMessage(null);
  }

  function beginLinkOutcome(group: AgendaGroup) {
    const item = group.latest;
    setAction("LINK_OUTCOME");
    setForm({
      agenda_item_id: text(item.agenda_item_id),
      expected_version: String(toNumber(item.version) || 1),
      event_id: text(item.linked_event_id, ""),
      report_id: text(item.linked_report_id, ""),
      evidence_id: text(item.linked_evidence_id, ""),
      outcome_occurred_at: toInputDate(item.outcome_occurred_at),
      outcome_note: text(item.outcome_note, ""),
      subject_id: text(item.subject_id, ""),
      instrument_id: text(item.instrument_id, ""),
      kind: "",
      title: "",
      fiscal_period: "",
      timezone: "UTC",
      date_certainty: "UNKNOWN",
      window_start: "",
      window_end: "",
      expected_question: "",
      source_reference: "",
      revision_note: "",
      cancellation_reason: "",
    });
    setFormVisible(true);
    setError(null);
    setMessage(null);
  }

  function applyOutcomeCandidate(candidateKey: string) {
    const candidate = outcomeCandidates.find((item) => (
      `${text(item.entity_type, "").toLowerCase()}:${text(item.entity_id, "")}` === candidateKey
    ));
    if (!candidate) return;
    const entityType = text(candidate.entity_type, "").toLowerCase();
    const entityId = text(candidate.entity_id, "");
    setForm((value) => ({
      ...value,
      event_id: entityType === "event" ? entityId : value.event_id,
      report_id: entityType === "report" ? entityId : value.report_id,
      evidence_id: entityType === "evidence" ? entityId : value.evidence_id,
      outcome_occurred_at: (
        text(candidate.occurred_at, "")
          ? toInputDate(candidate.occurred_at)
          : value.outcome_occurred_at
      ),
    }));
  }

  async function executeAgendaAction() {
    if (action === "CREATE" && !form.title.trim()) {
      setError("Title is required.");
      return;
    }
    if (action === "LINK_OUTCOME" && !form.agenda_item_id.trim()) {
      setError("Agenda item id is required.");
      return;
    }
    if ((action === "REVISE" || action === "CANCEL") && !form.agenda_item_id.trim()) {
      setError("Agenda item id is required.");
      return;
    }
    if (
      action === "LINK_OUTCOME"
      && !form.event_id.trim()
      && !form.report_id.trim()
      && !form.evidence_id.trim()
    ) {
      setError("At least one of event ID, report ID, or evidence ID is required.");
      return;
    }
    if (
      action === "LINK_OUTCOME"
      && !form.event_id.trim()
      && !form.outcome_occurred_at.trim()
    ) {
      setError("Outcome occurred time is required.");
      return;
    }
    if (action === "LINK_OUTCOME" && !form.outcome_note.trim()) {
      setError("Outcome note is required so the transition remains auditable.");
      return;
    }
    if (action === "CANCEL" && !form.cancellation_reason.trim()) {
      setError("Cancellation reason is required.");
      return;
    }
    if (action !== "CANCEL" && action !== "LINK_OUTCOME") {
      const hasScope = Boolean(form.subject_id.trim()) || Boolean(form.instrument_id.trim());
      const allowsGlobalScope = form.kind === "MACRO_RELEASE" || form.kind === "POLICY";
      if (!hasScope && !allowsGlobalScope) {
        setError("Either Research Subject id or Instrument id is required.");
        return;
      }
      if (form.date_certainty !== "UNKNOWN") {
        if (!form.window_start.trim() || !form.window_end.trim()) {
          setError("window_start and window_end are required unless date certainty is UNKNOWN.");
          return;
        }
        if (fromInputDate(form.window_end) < fromInputDate(form.window_start)) {
          setError("window_end must be >= window_start.");
          return;
        }
      }
    }
    if (!window.confirm(`Confirm Catalyst Agenda ${action.toLowerCase()}?`)) return;

    setBusy(action);
    setError(null);
    setMessage(null);
    try {
      const request = buildRequest(action, form);
      const response = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "research_memory_append",
        arguments: {
          request,
        },
        confirmation: "research_memory_append",
      });
      const result = asDict(response.result);
      if (result.ok === false) {
        const first = listOf<Dict>(result, "errors")[0];
        throw new Error(text(first?.message, "Agenda mutation failed."));
      }
      setMessage(`Catalyst Agenda ${action} submitted.`);
      setFormVisible(false);
      setForm(EMPTY_FORM);
      agendaApi.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to submit agenda mutation.");
    } finally {
      setBusy(null);
    }
  }

  function parseSummaryPreviewBody(value: string): string {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }

  async function previewSummary() {
    setSummaryBusy("preview");
    setSummaryError(null);
    setSummaryMessage(null);
    setSummarySendResult(null);
    try {
      const response = await fetch(`${API_BASE}/api/agenda/summary-preview`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.text();
      setSummaryPreview(parseSummaryPreviewBody(body));
    } catch (cause) {
      setSummaryError(cause instanceof Error ? cause.message : "Unable to preview daily agenda summary.");
    } finally {
      setSummaryBusy(null);
    }
  }

  async function sendSummary() {
    setSummaryBusy("send");
    setSummaryError(null);
    setSummaryMessage(null);
    setSummarySendResult(null);
    try {
      const response = await postApi<Dict>("/api/agenda/summary-send", {});
      setSummarySendResult(parseSummaryPreviewBody(JSON.stringify(response)));
      setSummaryMessage("Daily summary queued for delivery.");
    } catch (cause) {
      setSummaryError(cause instanceof Error ? cause.message : "Unable to queue daily agenda summary.");
    } finally {
      setSummaryBusy(null);
    }
  }

  async function runAgendaProviderSync() {
    const windowDays = asInt(syncPayload.windowDays, 30);
    if (windowDays < 1 || windowDays > 180) {
      setSyncError("window_days must be between 1 and 180.");
      return;
    }
    if (!window.confirm("Run Catalyst Agenda provider sync?")) return;

    setSyncBusy(true);
    setSyncError(null);
    setSyncMessage(null);
    try {
      const request = {
        window_days: windowDays,
        instrument_ids: splitIdentifierList(syncPayload.instrumentIds),
        fred_release_ids: splitIdentifierList(syncPayload.fredReleaseIds),
        idempotency_key: `agenda-sync-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
      };
      const response = await postApi<AgendaSyncReceipt>("/api/agenda/sync", request);
      const receipt = unwrapAgendaSync(response);
      if (receipt === null) {
        throw new Error("Unable to parse provider sync receipt.");
      }
      setLastSync(receipt);
      setSyncMessage(`Sync completed: ${receipt.receipt_id}`);
      agendaApi.refresh();
    } catch (cause) {
      setSyncError(cause instanceof Error ? cause.message : "Unable to run provider sync.");
    } finally {
      setSyncBusy(false);
    }
  }

  async function loadVersionHistory(agendaItemId: string) {
    setHistoryLoading(agendaItemId);
    setError(null);
    try {
      const query = new URLSearchParams({
        agenda_item_id: agendaItemId,
        include_history: "true",
        window_days: "180",
        limit: "200",
        offset: "0",
      });
      const response = await fetch(`${API_BASE}/api/agenda?${query.toString()}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = (await response.json()) as Dict;
      setHistoryById((current) => ({
        ...current,
        [agendaItemId]: extractItems(payload).sort(
          (left, right) => toNumber(right.version) - toNumber(left.version),
        ),
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load Agenda history.");
    } finally {
      setHistoryLoading(null);
    }
  }

  const syncProviderFailures = (receipt: AgendaSyncReceipt | null): string[] => {
    if (receipt === null) return [];
    return receipt.provider_results
      .filter((result) => result.status.toUpperCase() === "FAILED")
      .map((result) =>
        `${result.scope_ref} (${result.vendor})` + (result.error_code ? `: ${result.error_code}` : ""),
      );
  };

  const syncWarnings = (receipt: AgendaSyncReceipt | null): string[] => {
    if (receipt === null) return [];
    const codes = new Set(receipt.limitation_codes);
    for (const result of receipt.provider_results) {
      for (const code of listOf<string>(result, "warning_codes")) {
        codes.add(code);
      }
    }
    return Array.from(codes);
  };

  return (
    <ConsoleShell active="agenda" eyebrow="Catalyst schedule and review" title="Catalyst Agenda">
      <DataBoundary loading={agendaApi.loading} error={agendaApi.error}>
        <div className="page-actions">
          <RefreshButton onClick={agendaApi.refresh} loading={agendaApi.loading} />
          <ActionButton onClick={() => { void beginCreate(); }}>Create Agenda Item</ActionButton>
          <ActionButton busy={summaryBusy === "preview"} onClick={() => { void previewSummary(); }}>
            Preview Daily Summary
          </ActionButton>
          <ActionButton busy={summaryBusy === "send"} onClick={() => { void sendSummary(); }}>
            Queue Daily Summary
          </ActionButton>
        </div>

        {message && <p className="card-note">{message}</p>}
        {syncMessage && <p className="card-note">{syncMessage}</p>}
        {summaryMessage && <p className="card-note">{summaryMessage}</p>}
        {summaryError && <div className="inline-error">{summaryError}</div>}
        {summaryPreview !== null && (
          <Card className="span-12" kicker="AGENDA DAILY SUMMARY" title="Daily summary preview">
            <p className="agenda-note">Read-only preview from <strong>/api/agenda/summary-preview</strong>.</p>
            <pre className="agenda-summary-preview" aria-label="Daily summary preview">
              {summaryPreview}
            </pre>
          </Card>
        )}
        {summarySendResult !== null && (
          <Card className="span-12" kicker="AGENDA DAILY SUMMARY" title="Daily summary queue receipt">
            <pre className="agenda-summary-preview" aria-label="Daily summary queue receipt">
              {summarySendResult}
            </pre>
          </Card>
        )}

        <Card className="span-12" kicker="AGEND A PROVIDER SYNC" title="Provider sync">
          <p className="agenda-note">
            Explicitly sync provider calendars (manual only). This does not refresh accounts or watchlists.
          </p>
          <div className="agenda-sync-grid">
            <label>
              <span>Window days</span>
              <input
                type="number"
                min={1}
                max={180}
                value={syncPayload.windowDays}
                onChange={(event) =>
                  setSyncPayload((value) => ({ ...value, windowDays: event.target.value }))
                }
              />
            </label>
            <label>
              <span>Instrument IDs (comma/new-line separated)</span>
              <textarea
                rows={2}
                value={syncPayload.instrumentIds}
                onChange={(event) =>
                  setSyncPayload((value) => ({ ...value, instrumentIds: event.target.value }))
                }
              />
            </label>
            <label>
              <span>FRED release IDs (repeatable, comma/new-line separated)</span>
              <textarea
                rows={2}
                value={syncPayload.fredReleaseIds}
                onChange={(event) =>
                  setSyncPayload((value) => ({ ...value, fredReleaseIds: event.target.value }))
                }
              />
            </label>
          </div>
          <div className="agenda-editor-actions">
            <ActionButton busy={syncBusy} onClick={() => { void runAgendaProviderSync(); }}>
              Sync Provider Calendars
            </ActionButton>
          </div>
          {syncError && <div className="inline-error">{syncError}</div>}
          {lastSync ? (
            <div className="agenda-sync-receipt">
              <h3>Last sync receipt</h3>
              <div className="agenda-sync-receipt-grid">
                <div><span>Run</span><strong>{text(lastSync.receipt_id)}</strong></div>
                <div><span>Status</span><strong>{text(lastSync.status)}</strong></div>
                <div><span>Started</span><strong>{formatDate(lastSync.started_at)}</strong></div>
                <div><span>Revised</span><strong>{String(lastSync.revised_count)}</strong></div>
                <div><span>Unchanged</span><strong>{String(lastSync.unchanged_count)}</strong></div>
                <div><span>Appended</span><strong>{String(lastSync.appended_count)}</strong></div>
                <div><span>Date drift</span><strong>{String(lastSync.date_drift_count)}</strong></div>
                <div><span>Failures</span><strong>{String(lastSync.failed_scope_count)}</strong></div>
              </div>
              {syncProviderFailures(lastSync).length > 0 && (
                <p>
                  <strong>Provider failures</strong>
                  {" "}
                  <span>{syncProviderFailures(lastSync).join(" ; ")}</span>
                </p>
              )}
              {syncWarnings(lastSync).length > 0 && (
                <p>
                  <strong>Warnings</strong>
                  {" "}
                  <span>{syncWarnings(lastSync).join(" ; ") || "—"}</span>
                </p>
              )}
            </div>
          ) : null}
        </Card>

        <Card className="span-12" kicker="AGENDA PULSE" title="Next 7D / upcoming / overdue / coverage gap">
          <div className="agenda-summary-grid">
            <div><span>Upcoming 7D</span><strong>{String(summary.upcoming7d)}</strong></div>
            <div><span>Upcoming</span><strong>{String(summary.upcoming)}</strong></div>
            <div><span>Overdue</span><strong>{String(summary.overdue)}</strong></div>
            <div><span>Coverage gap</span><strong>{String(summary.coverageGap)}</strong></div>
          </div>
          <div className="agenda-summary-nav">
            <p>Use full manager below: scope/kind/status filters, editable Draft/Revise/Cancel, and version history per durable item.</p>
            <Link className="text-link" href="#agenda-detail">Jump to manager →</Link>
          </div>
        </Card>

        <Card id="agenda-detail" className="span-12" kicker="DETERMINISTIC EDITING GUARD" title="Agenda manager">
          <p className="agenda-note">
            Mutations are routed through <strong>research_memory_append</strong> and the Agenda operation contract.
            Every action creates an append-only version. Only an explicitly confirmed LINK_OUTCOME may move an UPCOMING item to OCCURRED after its durable Event, Report, or Evidence passes ownership and visibility checks.
          </p>

          <div className="agenda-toolbar">
            <label>
              <span>Time bucket</span>
              <select value={timeFilter} onChange={(event) => setTimeFilter(event.target.value as (typeof TIME_FILTERS)[number])}>
                {TIME_FILTERS.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <label>
              <span>Kind</span>
              <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>
                {kindOptions.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <label>
              <span>Scope</span>
              <select value={scopeFilter} onChange={(event) => setScopeFilter(event.target.value as (typeof SCOPE_OPTIONS)[number])}>
                {scopeOptions.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <label>
              <span>Status</span>
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                {statusOptions.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
          </div>

          {error && <div className="inline-error">{error}</div>}

          {formVisible ? (
            <section className="agenda-editor">
            <h3>
                {action === "CREATE" ? "Create Catalyst Agenda item" : action === "REVISE" ? "Revise Catalyst Agenda item" : action === "LINK_OUTCOME" ? (form.outcome_occurred_at ? "Revise Agenda outcome" : "Link Agenda outcome") : "Cancel Catalyst Agenda item"}
              </h3>
              <div className="agenda-editor-grid">
                {action === "CREATE" || action === "REVISE" ? (
                  <>
                    <label>
                      <span>Title</span>
                      <input
                        value={form.title}
                        onChange={(event) => setForm((value) => ({ ...value, title: event.target.value }))}
                        placeholder="e.g., TTWO guidance call"
                      />
                    </label>
                    <label>
                      <span>Kind</span>
                      <select value={form.kind} onChange={(event) => setForm((value) => ({ ...value, kind: event.target.value }))}>
                        {KIND_OPTIONS.map((item) => <option key={item}>{item}</option>)}
                      </select>
                    </label>
                    <label>
                      <span>Date certainty</span>
                      <select value={form.date_certainty} onChange={(event) => setForm((value) => ({ ...value, date_certainty: event.target.value }))}>
                        {DATE_CERTAINTY_OPTIONS.map((item) => <option key={item}>{item}</option>)}
                      </select>
                    </label>
                    <label>
                      <span>Timezone</span>
                      <select value={form.timezone} onChange={(event) => setForm((value) => ({ ...value, timezone: event.target.value }))}>
                        {TIMEZONE_OPTIONS.map((item) => <option key={item}>{item}</option>)}
                      </select>
                    </label>
                    <label>
                      <span>Fiscal period</span>
                      <input value={form.fiscal_period} onChange={(event) => setForm((value) => ({ ...value, fiscal_period: event.target.value }))} />
                    </label>
                    <label>
                      <span>Research Subject ID</span>
                      <input
                        value={form.subject_id}
                        onChange={(event) => setForm((value) => ({ ...value, subject_id: event.target.value }))}
                        list="agenda-subject-list"
                        placeholder="case_..."
                      />
                    </label>
                    <label>
                      <span>Instrument ID</span>
                      <input
                        value={form.instrument_id}
                        onChange={(event) => setForm((value) => ({ ...value, instrument_id: event.target.value }))}
                        placeholder="equity:US:AAPL"
                      />
                    </label>
                    <label>
                      <span>Window start</span>
                      <input
                        type="datetime-local"
                        value={form.window_start}
                        onChange={(event) => setForm((value) => ({ ...value, window_start: event.target.value }))}
                        disabled={form.date_certainty === "UNKNOWN"}
                      />
                    </label>
                    <label>
                      <span>Window end</span>
                      <input
                        type="datetime-local"
                        value={form.window_end}
                        onChange={(event) => setForm((value) => ({ ...value, window_end: event.target.value }))}
                        disabled={form.date_certainty === "UNKNOWN"}
                      />
                    </label>
                    <label>
                      <span>Source reference</span>
                      <input
                        value={form.source_reference}
                        onChange={(event) => setForm((value) => ({ ...value, source_reference: event.target.value }))}
                      />
                    </label>
                    <label className="agenda-textarea-field">
                      <span>Expected question</span>
                      <textarea rows={2} value={form.expected_question} onChange={(event) => setForm((value) => ({ ...value, expected_question: event.target.value }))} />
                    </label>
                    <label className="agenda-textarea-field">
                      <span>Revision note</span>
                      <textarea rows={2} value={form.revision_note} onChange={(event) => setForm((value) => ({ ...value, revision_note: event.target.value }))} />
                    </label>
                    <label>
                      <span>Source</span>
                      <input value="USER_CONFIRMED" disabled />
                    </label>
                    <label>
                      <span>Status</span>
                      <input value="UPCOMING" disabled />
                    </label>
                  </>
                ) : action === "LINK_OUTCOME" ? (
                  <>
                    <label>
                      <span>Agenda item ID</span>
                      <input value={form.agenda_item_id} disabled />
                    </label>
                    <label>
                      <span>Expected version</span>
                      <input value={form.expected_version} disabled />
                    </label>
                    <label className="agenda-textarea-field">
                      <span>Choose durable research fact</span>
                      <select
                        defaultValue=""
                        onChange={(event) => applyOutcomeCandidate(event.target.value)}
                        disabled={candidateApi.loading || outcomeCandidates.length === 0}
                      >
                        <option value="">
                          {candidateApi.loading ? "Loading candidates…" : "Select Event / Report / Evidence"}
                        </option>
                        {outcomeCandidates.map((candidate) => {
                          const entityType = text(candidate.entity_type, "").toLowerCase();
                          const entityId = text(candidate.entity_id, "");
                          return (
                            <option key={`${entityType}:${entityId}`} value={`${entityType}:${entityId}`}>
                              {entityType.toUpperCase()} · {text(candidate.title, shortId(entityId))} · {formatDate(candidate.occurred_at ?? candidate.visible_at)}
                            </option>
                          );
                        })}
                      </select>
                      {candidateApi.error ? <small>Candidate lookup unavailable: {candidateApi.error}</small> : null}
                    </label>
                    <label>
                      <span>Linked event ID</span>
                      <input
                        value={form.event_id}
                        onChange={(event) => setForm((value) => ({ ...value, event_id: event.target.value }))}
                        placeholder="event_..."
                      />
                    </label>
                    <label>
                      <span>Linked report ID</span>
                      <input
                        value={form.report_id}
                        onChange={(event) => setForm((value) => ({ ...value, report_id: event.target.value }))}
                        placeholder="report_..."
                      />
                    </label>
                    <label>
                      <span>Linked evidence ID</span>
                      <input
                        value={form.evidence_id}
                        onChange={(event) => setForm((value) => ({ ...value, evidence_id: event.target.value }))}
                        placeholder="evidence_..."
                      />
                    </label>
                    <label>
                      <span>Outcome occurred at</span>
                      <input
                        type="datetime-local"
                        value={form.outcome_occurred_at}
                        onChange={(event) => setForm((value) => ({ ...value, outcome_occurred_at: event.target.value }))}
                        required
                      />
                    </label>
                    <label className="agenda-textarea-field">
                      <span>Outcome note</span>
                      <textarea
                        rows={3}
                        value={form.outcome_note}
                        onChange={(event) => setForm((value) => ({ ...value, outcome_note: event.target.value }))}
                        placeholder="Why these durable facts close or revise the Agenda outcome"
                        required
                      />
                    </label>
                  </>
                ) : (
                  <>
                    <label>
                      <span>Agenda item ID</span>
                      <input value={form.agenda_item_id} disabled />
                    </label>
                    <label>
                      <span>Expected version</span>
                      <input value={form.expected_version} disabled />
                    </label>
                    <label className="agenda-textarea-field">
                      <span>Cancellation reason</span>
                      <textarea
                        rows={3}
                        value={form.cancellation_reason}
                        onChange={(event) => setForm((value) => ({ ...value, cancellation_reason: event.target.value }))}
                      />
                    </label>
                  </>
                )}
              </div>
              <div className="agenda-editor-actions">
                <ActionButton busy={busy === action} onClick={() => { void executeAgendaAction(); }}>
                  {action === "CREATE" ? "Create" : action === "REVISE" ? "Revise" : action === "LINK_OUTCOME" ? (form.outcome_occurred_at ? "Revise outcome" : "Link outcome") : "Cancel"}
                </ActionButton>
                <ActionButton tone="warning" onClick={() => setFormVisible(false)}>Dismiss</ActionButton>
              </div>
            </section>
          ) : null}

          <datalist id="agenda-subject-list">
            {subjects.map((subject) => {
              const id = text(subject.case_id) || text(subject.subject_id);
              if (!id) return null;
              return <option key={id} value={id}>{text(subject.title, id)}</option>;
            })}
          </datalist>

          {filtered.length === 0 ? (
            <Empty>No agenda items match current filters.</Empty>
          ) : (
            <div className="agenda-list">
              {filtered.map((group) => {
                const latest = group.latest;
                const warnings = asStringList(latest.warning_codes);
                const limitations = asStringList(latest.limitation_codes);
                const sourceVendor = text(latest.source_vendor, text(latest.source_type, "USER_CONFIRMED"));
                const sourceType = text(latest.source_type, "");
                const sourceText = sourceType ? `${sourceType}/${sourceVendor}` : sourceVendor;
                const badge = classify(latest, nowMs);
                const scope = group.scope;
                const status = text(latest.status, "UPCOMING");
                const statusIsUpcoming = status === "UPCOMING";
                const statusIsOccurred = status === "OCCURRED";
                const linkedEventIds = [...asSingleStringList(latest.linked_event_id), ...asStringList(latest.linked_event_ids)];
                const linkedReportIds = [...asSingleStringList(latest.linked_report_id), ...asStringList(latest.linked_report_ids)];
                const linkedEvidenceIds = [...asSingleStringList(latest.linked_evidence_id), ...asStringList(latest.linked_evidence_ids)];
                const resolvedEvidenceIds = [...asSingleStringList(latest.resolved_evidence_id), ...asStringList(latest.resolved_evidence_ids)];
                const windowText = (() => {
                  const start = text(latest.window_start, "—");
                  const end = text(latest.window_end, "—");
                  if (start === "—" && end === "—") return "None";
                  return `${start} → ${end}`;
                })();
                const versions = historyById[group.agenda_item_id] ?? group.versions;

                return (
                  <article className="agenda-item" key={group.agenda_item_id}>
                    <div className="agenda-item-header">
                      <div>
                        <strong>{text(latest.title, "Untitled Agenda Item")}</strong>
                        <small>
                          {shortId(group.agenda_item_id)} · v{text(latest.version, "1")} · {sourceText}
                        </small>
                      </div>
                      <div className="agenda-item-badges">
                        <Badge value={badge.badge} />
                        <Badge value={text(latest.kind, "USER_DEFINED")} />
                        <Badge value={text(latest.status, "UPCOMING")} />
                      </div>
                    </div>

                    <div className="agenda-item-grid">
                      <div><span>Subject / Instrument</span><strong>{shortId(latest.subject_id)} / {shortId(latest.instrument_id)}</strong></div>
                      <div><span>Window / Certainty</span><strong>{windowText}</strong><small>{text(latest.date_certainty, "UNKNOWN")}</small></div>
                      <div><span>Scope</span><strong>{scope}</strong></div>
                      <div><span>Expected question</span><strong>{text(latest.expected_question, "—")}</strong></div>
                      <div><span>Warnings</span><strong>{warnings.length > 0 ? warnings.join(", ") : "None"}</strong></div>
                      <div><span>Limitations</span><strong>{limitations.length > 0 ? limitations.join(", ") : "None"}</strong></div>
                      <div><span>Fiscal / Timezone</span><strong>{text(latest.fiscal_period, "—")} / {text(latest.timezone, "UTC")}</strong></div>
                      <div><span>Recorded</span><strong>{formatDate(latest.recorded_at)}</strong></div>
                    </div>

                    <div className="agenda-item-grid">
                      <div><span>Source</span><strong>{sourceText}</strong></div>
                      <div><span>Status</span><strong>{status}</strong></div>
                      <div><span>Revision note</span><strong>{text(latest.revision_note, "—")}</strong></div>
                      <div><span>Source reference</span><strong>{text(latest.source_reference, "—")}</strong></div>
                    </div>
                    {statusIsOccurred ? (
                      <div className="agenda-item-grid">
                        <div><span>Outcome occurred</span><strong>{formatDate(latest.outcome_occurred_at)}</strong></div>
                        <div><span>Outcome note</span><strong>{text(latest.outcome_note, "—")}</strong></div>
                        <div><span>Linked event ID</span><strong>{linkedEventIds.length > 0 ? linkedEventIds.join(", ") : "—"}</strong></div>
                        <div><span>Linked report ID</span><strong>{linkedReportIds.length > 0 ? linkedReportIds.join(", ") : "—"}</strong></div>
                        <div><span>Linked evidence ID</span><strong>{linkedEvidenceIds.length > 0 ? linkedEvidenceIds.join(", ") : "—"}</strong></div>
                        <div><span>Resolved supporting evidence</span><strong>{resolvedEvidenceIds.length > 0 ? resolvedEvidenceIds.join(", ") : "—"}</strong></div>
                      </div>
                    ) : null}

                    <div className="agenda-item-actions">
                      {statusIsUpcoming ? (
                        <>
                          <ActionButton onClick={() => { beginRevise(group); }}>Revise</ActionButton>
                          <ActionButton onClick={() => { beginLinkOutcome(group); }}>Link Outcome</ActionButton>
                          <ActionButton tone="warning" onClick={() => { beginCancel(group); }}>Cancel</ActionButton>
                        </>
                      ) : null}
                      {statusIsOccurred ? (
                        <ActionButton onClick={() => { beginLinkOutcome(group); }}>Revise Outcome</ActionButton>
                      ) : null}
                      <ActionButton
                        busy={historyLoading === group.agenda_item_id}
                        onClick={() => { void loadVersionHistory(group.agenda_item_id); }}
                      >
                        Load Version History
                      </ActionButton>
                    </div>

                    <details className="agenda-versions">
                      <summary>Version history ({Math.max(versions.length - 1, 0)})</summary>
                      {versions.length <= 1 ? (
                        <p className="agenda-empty">No additional history versions returned.</p>
                      ) : (
                        <ul>
                          {versions.slice(1).map((versionItem) => (
                            <li key={`${text(versionItem.agenda_item_id)}-${text(versionItem.version, "0")}-${text(versionItem.recorded_at)}`}>
                              v{text(versionItem.version)} · {text(versionItem.status, "UPCOMING")} · {formatDate(versionItem.recorded_at)} · window {text(versionItem.window_start, "—")} → {text(versionItem.window_end, "—")}
                            </li>
                          ))}
                        </ul>
                      )}
                    </details>
                  </article>
                );
              })}
            </div>
          )}
          <div className="page-actions">
            <ActionButton disabled={agendaOffset === 0} onClick={() => setAgendaOffset(Math.max(0, agendaOffset - 200))}>Previous 200</ActionButton>
            <small>{agendaTotal || grouped.length} total · page {Math.floor(agendaOffset / 200) + 1}</small>
            <ActionButton disabled={!agendaHasMore} onClick={() => setAgendaOffset(agendaOffset + 200)}>Next 200</ActionButton>
          </div>
        </Card>
      </DataBoundary>
    </ConsoleShell>
  );
}
