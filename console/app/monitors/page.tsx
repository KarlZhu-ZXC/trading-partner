"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { Play, Plus, RefreshCw } from "lucide-react";
import { ConsoleShell } from "../components/console-shell";
import { EntityBrowser } from "../components/entity-browser";
import { ConfirmationDialog, ErrorNote, ActionButton, Badge, Card, DataBoundary, Empty, HorizontalTabs, PageActionMenu, displayJson, formatDate, formatDecimal, monitorAnchorId, shortId } from "../components/ui";
import { envelopeData, listOf, postApi, useApi } from "../lib/api";
import { monitorRunPresentation } from "../lib/monitor-runs";
import { useAgentPageContext } from "../lib/agent-page-context";
import { MonitorEditor } from "./monitor-editor";

type Dict = Record<string, unknown>;
type ConfirmationState = { title: string; description: string; confirmLabel?: string; tone?: "default" | "warning"; onConfirm: () => void };
type MonitorPriceObservation = {
  kind: "available" | "unavailable" | "mixed";
  value: unknown;
  factAsOf: unknown;
  extendedHours: boolean;
};

const MONITOR_STATUSES = ["ALL", "ACTIVE", "PAUSED", "ARCHIVED"] as const;
const MONITOR_PAGE_SIZE = {
  initial: 6,
  getSize: (width: number) => width <= 700 ? 1 : width <= 1050 ? 4 : 6,
  target: "viewport" as const,
};
const MONITOR_MODULES = [
  { id: "overview", label: "Overview" },
  { id: "rules", label: "Rules" },
  { id: "runs", label: "Runs" },
  { id: "events", label: "Events" },
] as const;
type MonitorModule = (typeof MONITOR_MODULES)[number]["id"];

function compactMonitorRule(rule: Dict): Dict {
  const allowed = [
    "rule_code",
    "description",
    "rule_type",
    "severity",
    "instrument_id",
    "price_threshold",
    "risk_status_threshold",
    "max_fact_age_seconds",
    "fact_type",
    "metric_key",
    "comparator",
    "numeric_threshold",
    "recovery_threshold",
    "technical_interval",
    "event_after",
  ];
  return Object.fromEntries(
    allowed
      .filter((key) => rule[key] !== null && rule[key] !== undefined)
      .map((key) => [key, rule[key]]),
  );
}

function lifecycleUpdateRequest(monitor: Dict, status: "ACTIVE" | "PAUSED"): Dict {
  const optional = (key: string, outputKey = key) => (
    monitor[key] === null || monitor[key] === undefined
      ? {}
      : { [outputKey]: monitor[key] }
  );
  return {
    operation: "update",
    monitor_id: monitor.monitor_id,
    expected_version: monitor.version,
    name: monitor.name,
    cadence: monitor.cadence,
    status,
    rules: listOf<Dict>(monitor, "rules").map(compactMonitorRule),
    confirmed_by: "user",
    idempotency_key: `console-monitor-${status.toLowerCase()}-${String(monitor.monitor_id)}-v${String(monitor.version)}`,
    ...optional("primary_instrument_id"),
    ...optional("subject_id", "case_id"),
    ...optional("trade_plan_id"),
    ...optional("trade_plan_version"),
    ...optional("interval_minutes"),
    ...optional("valid_until"),
    ...optional("judgment_policy"),
  };
}

function MonitorFlipSurface({
  flipped,
  front,
  back,
}: {
  flipped: boolean;
  front: ReactNode;
  back: ReactNode;
}) {
  const frontRef = useRef<HTMLDivElement>(null);
  const backRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number>();

  useLayoutEffect(() => {
    const activeFace = flipped ? backRef.current : frontRef.current;
    if (!activeFace) return;
    const measure = () => setHeight(activeFace.scrollHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(activeFace);
    return () => observer.disconnect();
  }, [flipped]);

  return (
    <div
      className={`monitor-flip-surface${flipped ? " is-editing" : ""}`}
      style={height === undefined ? undefined : { height }}
    >
      <div className="monitor-flip-face monitor-flip-front" ref={frontRef} aria-hidden={flipped} inert={flipped}>
        {front}
      </div>
      <div className="monitor-flip-face monitor-flip-back" ref={backRef} aria-hidden={!flipped} inert={!flipped}>
        {back}
      </div>
    </div>
  );
}

function isPriceRule(rule: Dict): boolean {
  return ["PRICE_ABOVE", "PRICE_BELOW"].includes(String(rule.rule_type ?? ""))
    || String(rule.fact_type ?? "") === "PRICE";
}

function judgmentQuantityLabel(judgment: Dict): string {
  const minimum = Number(judgment.quantity_min ?? 0);
  const maximum = Number(judgment.quantity_max ?? 0);
  if (minimum === 0 && maximum === 0) return "0 shares · Wait";
  if (minimum === maximum) return `${minimum} shares`;
  return `${minimum}–${maximum} shares`;
}

function judgmentFeatureLabel(featureId: string): string {
  const latestPrice = featureId.match(/:([^:.]+)\.latest_price$/);
  if (latestPrice) return `${latestPrice[1]} · Latest price`;
  const quoteReturn = featureId.match(
    /:([^:.]+)\.(?:quote_return_pct|return_from_previous_regular_session_close_pct)$/,
  );
  if (quoteReturn) return `${quoteReturn[1]} · Extended-session change`;
  const relativeStrength = featureId.match(
    /^relative_strength\.([^.]+)\.(quote_return|return_from_previous_regular_session_close|return_1h|return_4h|return_1d)_spread_pct$/,
  );
  if (relativeStrength) {
    const windowLabel: Record<string, string> = {
      quote_return: "Extended-session",
      return_from_previous_regular_session_close: "Extended-session",
      return_1h: "1H",
      return_4h: "4H",
      return_1d: "1D",
    };
    return `${relativeStrength[1].replaceAll("_", "/")} · ${windowLabel[relativeStrength[2]]} relative spread`;
  }
  if (featureId === "quote_sessions_aligned") return "Latest quotes · Time aligned";
  if (featureId === "hourly_returns_aligned") return "Hourly returns · Window aligned";
  if (featureId === "daily_returns_aligned") return "Daily returns · Window aligned";
  return featureId.replaceAll("_", " ").replaceAll(".", " · ");
}

function CompositeJudgmentCard({ judgment }: { judgment: Dict }) {
  const sources = listOf<string>(judgment, "web_source_urls");
  const evidence = listOf<string>(judgment, "evidence_feature_ids");
  const warnings = listOf<string>(judgment, "warning_codes");
  const errors = listOf<string>(judgment, "error_codes");
  const urgency = String(judgment.urgency ?? judgment.status ?? "WATCH");
  const conclusion = String(judgment.conclusion ?? judgment.status ?? "—");

  return (
    <section className={`monitor-judgment-card ${urgency.toLowerCase()}`}>
      <header className="monitor-judgment-header">
        <div>
          <span className="monitor-judgment-kicker">Composite Judgment</span>
          <strong>{conclusion}</strong>
        </div>
        <div className="monitor-judgment-header-meta">
          <Badge value={urgency} />
          <time>{formatDate(judgment.created_at)}</time>
        </div>
      </header>

      <div className="monitor-judgment-current">
        <span>Current Read</span>
        <p>{String(judgment.market_state ?? judgment.summary ?? "No current judgment summary.")}</p>
      </div>

      <dl className="monitor-judgment-metrics">
        <div><dt>Phase</dt><dd>{String(judgment.phase ?? "—")}</dd></div>
        <div><dt>Divergence</dt><dd>{String(judgment.divergence ?? "—")}</dd></div>
        <div><dt>Suggested Change</dt><dd>{judgmentQuantityLabel(judgment)}</dd></div>
      </dl>

      <div className="monitor-judgment-callouts">
        <section className="next">
          <span>Next Trigger</span>
          <p>{String(judgment.next_trigger ?? "No next trigger supplied.")}</p>
        </section>
        <section className="invalid">
          <span>Invalidation</span>
          <p>{String(judgment.invalidation ?? "No invalidation supplied.")}</p>
        </section>
      </div>

      {(evidence.length > 0 || sources.length > 0 || warnings.length > 0 || errors.length > 0) && (
        <details className="monitor-judgment-evidence">
          <summary>Evidence & Diagnostics · {evidence.length + sources.length + warnings.length + errors.length}</summary>
          {evidence.length > 0 && <div className="monitor-judgment-features">
            <span>Deterministic Features</span>
            <ul>{evidence.map((feature) => <li key={feature} title={feature}>{judgmentFeatureLabel(feature)}</li>)}</ul>
          </div>}
          {sources.length > 0 && <div className="monitor-judgment-sources">
            <span>Web Sources</span>
            {sources.map((url) => <a href={url} key={url} rel="noreferrer" target="_blank">{url}</a>)}
          </div>}
          {warnings.length > 0 && <small className="warn">Warnings · {warnings.join(" · ")}</small>}
          {errors.length > 0 && <small className="bad">Errors · {errors.join(" · ")}</small>}
        </details>
      )}

      <footer>
        <span>{String(judgment.provider ?? "—")} / {String(judgment.model ?? "—")}</span>
        <span>No position or phase is changed automatically.</span>
      </footer>
    </section>
  );
}

function latestFactTime(observations: Dict[]): unknown {
  return observations.reduce<unknown>((latest, observation) => {
    const candidate = observation.fact_as_of;
    if (typeof candidate !== "string") return latest;
    if (typeof latest !== "string") return candidate;
    const candidateTime = new Date(candidate).getTime();
    const latestTime = new Date(latest).getTime();
    if (Number.isNaN(candidateTime)) return latest;
    return Number.isNaN(latestTime) || candidateTime > latestTime ? candidate : latest;
  }, null);
}

function monitorPriceObservation(monitor: Dict, rules: Dict[], latestRun: Dict, states: Dict[]): MonitorPriceObservation | null {
  const priceRuleCodes = new Set(rules.filter(isPriceRule).map((rule) => String(rule.rule_code)));
  if (priceRuleCodes.size === 0) return null;

  const monitorId = String(monitor.monitor_id ?? "");
  const runObservations = listOf<Dict>(latestRun, "observations").filter(
    (observation) => String(observation.monitor_id ?? "") === monitorId
      && priceRuleCodes.has(String(observation.rule_code ?? "")),
  );
  const observations = runObservations.length > 0
    ? runObservations
    : states.filter((state) => priceRuleCodes.has(String(state.rule_code ?? "")));
  const observedValues = [...new Set(
    observations
      .map((observation) => observation.observed_value)
      .filter((value) => value !== null && value !== undefined && value !== "")
      .map(String),
  )];
  const extendedHours = observations.some(
    (observation) => Array.isArray(observation.warning_codes)
      && observation.warning_codes.includes("EXTENDED_HOURS_PRICE"),
  );

  if (observedValues.length === 0) {
    return { kind: "unavailable", value: null, factAsOf: latestRun.completed_at, extendedHours };
  }
  if (observedValues.length > 1) {
    return { kind: "mixed", value: null, factAsOf: latestFactTime(observations), extendedHours };
  }
  return {
    kind: "available",
    value: observedValues[0],
    factAsOf: latestFactTime(observations),
    extendedHours,
  };
}

function ruleCondition(rule: Dict): string {
  const ruleType = String(rule.rule_type ?? "");
  if (ruleType === "PRICE_ABOVE") return `Above $${String(rule.price_threshold ?? "—")}`;
  if (ruleType === "PRICE_BELOW") return `Below $${String(rule.price_threshold ?? "—")}`;
  if (ruleType === "RISK_OVERALL_AT_LEAST") return `Portfolio risk at least ${String(rule.risk_status_threshold ?? "—")}`;
  const comparator = { GT: ">", GTE: "≥", LT: "<", LTE: "≤", EQ: "=", OCCURRED: "Occurred" }[String(rule.comparator ?? "")] ?? String(rule.comparator ?? "—");
  const threshold = rule.comparator === "OCCURRED" ? "" : ` ${String(rule.numeric_threshold ?? "—")}`;
  const interval = rule.fact_type === "TECHNICAL" ? ` · ${String(rule.technical_interval ?? "1d")}` : "";
  const recovery = rule.recovery_threshold === null || rule.recovery_threshold === undefined ? "" : ` · recover ${String(rule.recovery_threshold)}`;
  return `${String(rule.fact_type ?? "Fact")} · ${String(rule.metric_key ?? "—")}${interval} ${comparator}${threshold}${recovery}`;
}

function diagnosticStage(value: unknown): string {
  return {
    weekend_quote_request: "Weekend proxy quote request",
    weekend_quote: "Weekend proxy quote",
  }[String(value ?? "")] ?? String(value ?? "Provider request");
}

function diagnosticStatus(diagnostic: Dict): string {
  if (diagnostic.status_code !== null && diagnostic.status_code !== undefined) {
    return `HTTP ${String(diagnostic.status_code)}`;
  }
  if (diagnostic.status_class) return `HTTP ${String(diagnostic.status_class)}`;
  return "No HTTP response";
}

function monitorMatchesInstrument(item: Dict, query: string): boolean {
  if (!query) return true;
  const monitor = (item.monitor ?? {}) as Dict;
  const instrumentIds = [
    monitor.primary_instrument_id,
    ...listOf<Dict>(monitor, "rules").map((rule) => rule.instrument_id),
  ].filter((value): value is string => typeof value === "string" && value.length > 0);
  return instrumentIds.some((instrumentId) => {
    const normalizedId = instrumentId.toLocaleLowerCase();
    const symbol = String(shortId(instrumentId)).toLocaleLowerCase();
    return normalizedId.includes(query) || symbol.includes(query);
  });
}

export default function MonitorsPage() {
  const result = useApi<Dict>("/api/monitors?run_limit=30&event_limit=100");
  const [running, setRunning] = useState(false);
  const [runReceipt, setRunReceipt] = useState<unknown>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [editingMonitor, setEditingMonitor] = useState<Dict | null | undefined>(undefined);
  const [newMonitorTemplate, setNewMonitorTemplate] = useState<Dict | null>(null);
  const [archivingId, setArchivingId] = useState<string | null>(null);
  const [lifecycleId, setLifecycleId] = useState<string | null>(null);
  const [resolutionDraft, setResolutionDraft] = useState<{ eventId: string; action: "ACKNOWLEDGE" | "RESOLVE"; note: string; idempotencyKey: string } | null>(null);
  const [resolvingEvent, setResolvingEvent] = useState(false);
  const [resolutionError, setResolutionError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmationState | null>(null);
  const [instrumentFilter, setInstrumentFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<(typeof MONITOR_STATUSES)[number]>("ACTIVE");
  const [selectedMonitorId, setSelectedMonitorId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [activeModule, setActiveModule] = useState<MonitorModule>("overview");
  useAgentPageContext({
    surface: "monitors",
    selected_monitor_id: selectedMonitorId,
    selected_run_id: selectedRunId,
  });
  const dashboardEnvelope = (result.data?.dashboard as Dict | undefined) ?? {};
  const dashboard = envelopeData<Dict>(dashboardEnvelope);
  const runs = envelopeData<Dict>((result.data?.runs as Dict | undefined));
  const events = envelopeData<Dict>((result.data?.events as Dict | undefined));
  const dashboardItems = listOf<Dict>(dashboard, "items").filter((item) => (
    item._truncated !== true
    && item.monitor !== null
    && typeof item.monitor === "object"
    && typeof (item.monitor as Dict).monitor_id === "string"
  ));
  const dashboardTruncated = dashboardEnvelope._truncated === true;
  const items = dashboardItems.filter((item) => {
    const monitor = (item.monitor ?? {}) as Dict;
    return statusFilter === "ALL" || String(monitor.status ?? "").toUpperCase() === statusFilter;
  });
  const normalizedInstrumentFilter = instrumentFilter.trim().toLocaleLowerCase();
  const filteredItems = items.filter((item) => monitorMatchesInstrument(item, normalizedInstrumentFilter));
  const runItems = listOf<Dict>(runs, "runs");
  const eventItems = listOf<Dict>(events, "events");
  const selectedMonitor = dashboardItems.find((item) => String(((item.monitor ?? {}) as Dict).monitor_id ?? "") === selectedMonitorId) ?? null;
  const visibleRuns = selectedMonitorId ? runItems.filter((run) => [
    ...listOf<string>(run, "requested_monitor_ids"),
    ...listOf<string>(run, "selected_monitor_ids"),
    ...listOf<Dict>(run, "observations").map((observation) => String(observation.monitor_id ?? "")),
  ].includes(selectedMonitorId)) : runItems;
  const visibleEvents = selectedMonitorId ? eventItems.filter((event) => String(event.monitor_id ?? "") === selectedMonitorId) : eventItems;

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const tradePlanId = params.get("trade_plan_id");
    const tradePlanVersion = params.get("trade_plan_version");
    if (!tradePlanId || !tradePlanVersion) return;
    setNewMonitorTemplate({
      name: "Trade Plan Monitor",
      trade_plan_id: tradePlanId,
      trade_plan_version: Number(tradePlanVersion),
      compile_trade_plan_conditions: true,
      subject_id: params.get("subject_id") ?? "",
      primary_instrument_id: params.get("instrument_id") ?? "",
    });
    setEditingMonitor(null);
  }, []);


  function selectMonitor(monitorId: string) {
    setSelectedMonitorId(monitorId);
    setSelectedRunId(null);
    setActiveModule("overview");
    setEditingMonitor(undefined);
  }

  function clearMonitorFilters() {
    setInstrumentFilter("");
    setStatusFilter("ALL");
  }

  async function executeRunDue() {
    setRunning(true);
    setRunError(null);
    try {
      const receipt = await postApi<unknown>("/api/actions/run", {
        action: "monitor_run_due",
        confirmation: "monitor_run_due",
      });
      setRunReceipt(receipt);
      result.refresh();
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Run failed");
    } finally {
      setRunning(false);
    }
  }

  function runDue() {
    setConfirmation({
      title: "Evaluate Due Monitors",
      description: "Evaluate currently due Monitors? This may create events and send configured notifications.",
      confirmLabel: "Evaluate Due Monitors",
      onConfirm: () => { setConfirmation(null); void executeRunDue(); },
    });
  }

  async function executeArchiveMonitor(monitor: Dict) {
    const monitorId = String(monitor.monitor_id ?? "");
    if (!monitorId) return;
    setArchivingId(monitorId);
    setRunError(null);
    try {
      const response = await postApi<Dict>(`/api/monitors/${encodeURIComponent(monitorId)}/archive`, {
        expected_version: Number(monitor.version),
        confirmation: "monitor_archive",
      });
      if (response.ok === false) {
        const first = Array.isArray(response.errors) ? response.errors[0] as Dict | undefined : undefined;
        throw new Error(String(first?.message ?? "Unable to archive Monitor"));
      }
      if (String(editingMonitor?.monitor_id ?? "") === monitorId) setEditingMonitor(undefined);
      setRunReceipt(response);
      await result.refresh();
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Unable to archive Monitor");
    } finally {
      setArchivingId(null);
    }
  }

  function archiveMonitor(monitor: Dict) {
    const monitorId = String(monitor.monitor_id ?? "");
    if (!monitorId) return;
    const name = String(monitor.name ?? "Untitled Monitor");
    setConfirmation({
      title: "Archive Monitor",
      description: `Archive “${name}”? An ARCHIVED version will be appended. Historical versions, runs, and events will not be deleted.`,
      confirmLabel: "Archive Monitor",
      tone: "warning",
      onConfirm: () => { setConfirmation(null); void executeArchiveMonitor(monitor); },
    });
  }

  async function executeChangeMonitorStatus(monitor: Dict, status: "ACTIVE" | "PAUSED") {
    const monitorId = String(monitor.monitor_id ?? "");
    if (!monitorId) return;
    const action = status === "ACTIVE"
      ? String(monitor.status ?? "").toUpperCase() === "ARCHIVED" ? "restore and activate" : "reactivate"
      : "pause";
    setLifecycleId(monitorId);
    setRunError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "monitor_manage",
        arguments: { request: lifecycleUpdateRequest(monitor, status) },
        confirmation: "monitor_manage",
      });
      const envelope = response.result as Dict | undefined;
      if (envelope?.ok === false) {
        throw new Error(displayJson(envelope.errors ?? `Unable to ${action} Monitor`));
      }
      setRunReceipt(response);
      await result.refresh();
    } catch (error) {
      setRunError(error instanceof Error ? error.message : `Unable to ${action} Monitor`);
    } finally {
      setLifecycleId(null);
    }
  }

  function changeMonitorStatus(monitor: Dict, status: "ACTIVE" | "PAUSED") {
    const monitorId = String(monitor.monitor_id ?? "");
    if (!monitorId) return;
    const name = String(monitor.name ?? "Untitled Monitor");
    const action = status === "ACTIVE"
      ? String(monitor.status ?? "").toUpperCase() === "ARCHIVED" ? "restore and activate" : "reactivate"
      : "pause";
    setConfirmation({
      title: `${action[0].toUpperCase()}${action.slice(1)} Monitor`,
      description: `Confirm ${action} for “${name}”? A new ${status} version will be appended and all history will be preserved.`,
      confirmLabel: `${action[0].toUpperCase()}${action.slice(1)}`,
      tone: status === "PAUSED" ? "warning" : "default",
      onConfirm: () => { setConfirmation(null); void executeChangeMonitorStatus(monitor, status); },
    });
  }

  function beginResolution(eventId: string, action: "ACKNOWLEDGE" | "RESOLVE") {
    setResolutionError(null);
    setResolutionDraft({
      eventId,
      action,
      note: "",
      idempotencyKey: `console-event-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    });
  }

  async function executeResolution(draft: NonNullable<typeof resolutionDraft>, note: string) {
    setResolvingEvent(true);
    setResolutionError(null);
    try {
      const receipt = await postApi<unknown>("/api/tools/invoke", {
        tool_name: "monitor_manage",
        arguments: {
          request: {
            operation: "resolve_event",
            event_id: draft.eventId,
            action: draft.action,
            note,
            confirmed_by: "user",
            idempotency_key: draft.idempotencyKey,
          },
        },
        confirmation: "monitor_manage",
      });
      setRunReceipt(receipt);
      setResolutionDraft(null);
      result.refresh();
    } catch (error) {
      setResolutionError(error instanceof Error ? error.message : "Event update failed");
    } finally {
      setResolvingEvent(false);
    }
  }

  function submitResolution() {
    if (!resolutionDraft) return;
    const note = resolutionDraft.note.trim();
    if (!note) {
      setResolutionError("Enter a resolution note. It will become part of the immutable audit record.");
      return;
    }
    const actionLabel = resolutionDraft.action === "RESOLVE" ? "resolved" : "acknowledged";
    const draft = resolutionDraft;
    setConfirmation({
      title: `Confirm Event ${actionLabel[0].toUpperCase()}${actionLabel.slice(1)}`,
      description: `Mark event “${draft.eventId}” as ${actionLabel}? This appends an immutable Monitor event resolution record.`,
      confirmLabel: `Confirm ${actionLabel[0].toUpperCase()}${actionLabel.slice(1)}`,
      tone: actionLabel === "resolved" ? "warning" : "default",
      onConfirm: () => { setConfirmation(null); void executeResolution(draft, note); },
    });
  }

  const selectedDefinition = (selectedMonitor?.monitor ?? {}) as Dict;
  const selectedRules = listOf<Dict>(selectedDefinition, "rules");
  const selectedStates = listOf<Dict>(selectedMonitor ?? {}, "rule_states");
  const selectedStatesByCode = new Map(selectedStates.map((state) => [String(state.rule_code), state]));
  const selectedLatestRun = (selectedMonitor?.latest_run ?? {}) as Dict;
  const selectedJudgment = (selectedMonitor?.latest_judgment ?? {}) as Dict;
  const selectedPrice = selectedMonitor ? monitorPriceObservation(selectedDefinition, selectedRules, selectedLatestRun, selectedStates) : null;
  const selectedTriggeredCount = selectedStates.filter((state) => String(state.state ?? "").toUpperCase() === "TRIGGERED").length;
  const selectedUnavailableCount = selectedStates.filter((state) => String(state.state ?? "").toUpperCase() === "NOT_EVALUATED").length;
  const editingSelected = editingMonitor !== null && editingMonitor !== undefined && String(editingMonitor.monitor_id) === String(selectedDefinition.monitor_id);

  return (
    <ConsoleShell active="monitors" pageActions={<PageActionMenu ariaLabel="Monitors Page Actions" items={[
      { id: "new", label: "New Monitor", description: "Open a new durable Monitor definition", icon: <Plus aria-hidden="true" />, onSelect: () => { setNewMonitorTemplate(null); setEditingMonitor(null); } },
      { id: "run-due", label: running ? "Running Due Monitors…" : "Run Due Monitors", description: "Evaluate definitions that are currently due", icon: <Play aria-hidden="true" />, disabled: running, onSelect: () => { void runDue(); } },
      { id: "refresh", label: result.loading ? "Refreshing…" : "Refresh", description: "Reload durable Monitor data", icon: <RefreshCw aria-hidden="true" className={result.loading ? "spin" : undefined} />, disabled: result.loading, onSelect: result.refresh },
    ]} />}>
      <DataBoundary loading={result.loading} error={result.error}>
        {dashboardTruncated && <ErrorNote role="alert">Monitor Library is incomplete because its durable dashboard result was truncated. Refresh after restarting the local Console API.</ErrorNote>}
        <ErrorNote>{runError}</ErrorNote>
        <ErrorNote role="alert">{resolutionError}</ErrorNote>
        {runReceipt !== null && <details className="run-receipt"><summary>View Run Receipt</summary><pre>{displayJson(runReceipt)}</pre></details>}
        {editingMonitor === null && <MonitorEditor template={newMonitorTemplate ?? undefined} onClose={() => { setEditingMonitor(undefined); setNewMonitorTemplate(null); window.history.replaceState(null, "", "/monitors"); }} onSaved={(saved) => { setRunReceipt(saved); setEditingMonitor(undefined); setNewMonitorTemplate(null); window.history.replaceState(null, "", "/monitors"); result.refresh(); }} />}

        <div className="monitor-workbench">
          <Card
            className="monitor-list-panel monitor-library-card"
            kicker="MONITOR DEFINITIONS"
            title="Monitor Library"
            action={<div className="monitor-library-actions"><span className="monitor-filter-result" aria-live="polite">{filteredItems.length} / {dashboardItems.length}</span></div>}
          >
            <EntityBrowser
              items={dashboardItems}
              filteredItems={filteredItems}
              selectedId={selectedMonitorId}
              getId={(item) => String(((item.monitor ?? {}) as Dict).monitor_id ?? "")}
              onSelect={selectMonitor}
              onClearSelection={() => setSelectedMonitorId(null)}
              hashToId={(hash) => hash.match(/^#monitor-(.+)$/)?.[1] ?? null}
              search={{ value: instrumentFilter, onChange: setInstrumentFilter, label: "Target Filter", placeholder: "TTWO, TSLA, equity:US:TTWO", ariaLabel: "Filter Monitors by Target Symbol" }}
              status={{ value: statusFilter, onChange: (value) => setStatusFilter(value as (typeof MONITOR_STATUSES)[number]), label: "Status", ariaLabel: "Filter by Monitor Status", options: MONITOR_STATUSES.map((value) => ({ value, label: value === "ALL" ? "All (Including Archived)" : value })) }}
              onClearFilters={clearMonitorFilters}
              clearDisabled={!instrumentFilter && statusFilter === "ALL"}
              filteredNotice={<div className="entity-filter-notice" role="status"><span>The current Monitor is outside this filter.</span><button type="button" onClick={clearMonitorFilters}>Show Current</button></div>}
              emptyMessage={<Empty>No Monitor definitions yet.</Empty>}
              noMatchesMessage={<Empty>No Monitors match the current filters.</Empty>}
              listAriaLabel="Filtered Monitors"
              previousAriaLabel="Show Previous Monitors"
              nextAriaLabel="Show Next Monitors"
              rangeAriaLabel={(start, end, total) => `Showing Monitors ${start} through ${end} of ${total}`}
              responsivePageSize={MONITOR_PAGE_SIZE}
              hashKey="monitor"
              renderItem={(item, isSelected, select) => {
                const monitor = (item.monitor ?? {}) as Dict;
                const monitorId = String(monitor.monitor_id ?? "");
                const ruleStates = listOf<Dict>(item, "rule_states");
                const triggered = ruleStates.filter((state) => String(state.state ?? "").toUpperCase() === "TRIGGERED").length;
                const unavailable = ruleStates.filter((state) => String(state.state ?? "").toUpperCase() === "NOT_EVALUATED").length;
                return <button type="button" role="option" aria-selected={isSelected} id={`monitor-index-${monitorId}`} className={`monitor-index-item ${isSelected ? "selected" : ""}`} onClick={() => select(monitorId)} key={monitorId}><span className="monitor-index-status"><span className={`monitor-status-dot status-${String(monitor.status ?? "unknown").toLowerCase()}`} aria-hidden="true" />{String(monitor.status ?? "UNKNOWN")}</span><strong>{String(monitor.name ?? "Untitled Monitor")}</strong><small>{shortId(monitor.primary_instrument_id)} · {listOf<Dict>(monitor, "rules").length} rules</small><span className="monitor-index-health">{triggered > 0 ? `${triggered} triggered` : unavailable > 0 ? `${unavailable} unavailable` : "All quiet"}</span></button>;
              }}
            />
          </Card>

          <main className="monitor-detail" aria-live="polite">
            {!selectedMonitor ? <Empty>Select a Monitor above.</Empty> : <section className="monitor-card monitor-workspace-card" id={monitorAnchorId(selectedDefinition.monitor_id)}>
              <MonitorFlipSurface
                flipped={editingSelected}
                front={<div className="monitor-workspace-front">
                  <div className="monitor-title-row">
                    <div className="symbol-tile large">{shortId(selectedDefinition.primary_instrument_id)}</div>
                    <div className="monitor-copy"><h2>{String(selectedDefinition.name ?? "Untitled Monitor")}</h2><span className="mono">{String(selectedDefinition.monitor_id)} · v{String(selectedDefinition.version ?? "—")}</span></div>
                    <div className="monitor-title-actions">
                      {selectedDefinition.subject_id ? <Link href={`/decision-workbench?subject_id=${encodeURIComponent(String(selectedDefinition.subject_id))}&capture=decision`}>Record Decision</Link> : null}
                      <button type="button" onClick={() => setEditingMonitor(selectedDefinition)}>Edit</button>
                      {String(selectedDefinition.status ?? "").toUpperCase() === "ACTIVE" ? <button type="button" disabled={lifecycleId === String(selectedDefinition.monitor_id)} onClick={() => { void changeMonitorStatus(selectedDefinition, "PAUSED"); }}>{lifecycleId === String(selectedDefinition.monitor_id) ? "Working…" : "Pause"}</button> : <button className="restore-text" type="button" disabled={lifecycleId === String(selectedDefinition.monitor_id)} onClick={() => { void changeMonitorStatus(selectedDefinition, "ACTIVE"); }}>{lifecycleId === String(selectedDefinition.monitor_id) ? "Working…" : String(selectedDefinition.status ?? "").toUpperCase() === "ARCHIVED" ? "Restore & Activate" : "Activate"}</button>}
                      {String(selectedDefinition.status ?? "").toUpperCase() !== "ARCHIVED" && <button className="monitor-delete-button" type="button" disabled={archivingId === String(selectedDefinition.monitor_id)} onClick={() => { void archiveMonitor(selectedDefinition); }}>{archivingId === String(selectedDefinition.monitor_id) ? "Archiving…" : "Archive"}</button>}
                      <Badge value={String(selectedDefinition.status ?? "—")} />
                    </div>
                  </div>

                  <HorizontalTabs className="monitor-section-nav" items={MONITOR_MODULES} value={activeModule} onChange={setActiveModule} ariaLabel="Monitor modules" idPrefix="monitor-tab" panelIdPrefix="monitor-panel" />

                  <section id="monitor-panel-overview" className="monitor-module-panel" role="tabpanel" aria-labelledby="monitor-tab-overview" hidden={activeModule !== "overview"}>
                    <div className="monitor-runtime-strip">
                      {selectedPrice && <section className={`monitor-price-observation ${selectedPrice.kind}`} aria-label="Latest Run Price"><div className="monitor-price-value"><span>Latest Price</span><strong>{selectedPrice.kind === "available" ? formatDecimal(selectedPrice.value, 4) : "—"}</strong></div><div className="monitor-price-time"><span>{selectedPrice.kind === "mixed" ? "Observation State" : "Fact Time"}</span><strong>{selectedPrice.kind === "mixed" ? "Multiple price observations" : formatDate(selectedPrice.factAsOf)}</strong><small>{selectedPrice.kind === "unavailable" ? "No usable price" : selectedPrice.kind === "mixed" ? "Inspect individual price rules" : selectedPrice.extendedHours ? "Pre/Post Market" : "Close Observation"}</small></div></section>}
                      <div className="monitor-facts"><span>Cadence <strong>{String(selectedDefinition.cadence ?? "—")}</strong></span><span>Created <strong>{formatDate(selectedMonitor.monitor_created_at ?? selectedDefinition.created_at)}</strong></span><span>Last Edited <strong>{formatDate(selectedMonitor.monitor_updated_at ?? selectedDefinition.created_at)}</strong></span><span>Latest Run <strong>{formatDate(selectedLatestRun.completed_at)}</strong></span></div>
                    </div>
                    <div className="monitor-overview-metrics"><div><span>Rules</span><strong>{selectedRules.length}</strong><small>Durable Definition</small></div><div><span>Triggered</span><strong>{selectedTriggeredCount}</strong><small>Latest Known State</small></div><div><span>Unavailable</span><strong>{selectedUnavailableCount}</strong><small>Needs Data Review</small></div><div><span>Events</span><strong>{visibleEvents.length}</strong><small>Transition History</small></div></div>
                    {selectedJudgment.judgment_id ? <CompositeJudgmentCard judgment={selectedJudgment} /> : <div className="monitor-overview-empty"><strong>No Composite Judgment</strong><span>Deterministic rule states remain available in the Rules module.</span></div>}
                  </section>

                  <section id="monitor-panel-rules" className="monitor-module-panel" role="tabpanel" aria-labelledby="monitor-tab-rules" hidden={activeModule !== "rules"}><div className="rule-grid">{selectedRules.map((rule) => { const state = selectedStatesByCode.get(String(rule.rule_code)) ?? { rule_code: rule.rule_code, state: "NOT_EVALUATED" }; const showIndividualObservation = !isPriceRule(rule) || selectedPrice?.kind === "mixed"; return <article className={`rule-card ${String(state.state ?? "").toLowerCase()}`} key={String(state.rule_code)}><div className="rule-card-head"><span className="mono">{String(state.rule_code)}</span><div className="rule-card-meta"><span className={`rule-state ${String(state.state ?? "").toLowerCase()}`}>{String(state.state ?? "—")}</span><span className={`rule-severity ${String(rule.severity ?? "").toLowerCase()}`}>{String(rule.severity ?? "—")}</span></div></div><strong className="rule-description">{String(rule.description ?? "Legacy version has no human-readable meaning")}</strong><small className="rule-condition">{ruleCondition(rule)}</small>{showIndividualObservation && <span className="rule-observed">Current {String(state.observed_value ?? "N/A")}</span>}{showIndividualObservation && <time>{formatDate(state.fact_as_of)}</time>}</article>; })}</div></section>

                  <section id="monitor-panel-runs" className="monitor-module-panel" role="tabpanel" aria-labelledby="monitor-tab-runs" hidden={activeModule !== "runs"}><Card kicker="IMMUTABLE OBSERVATIONS" title={`Recent Runs · ${visibleRuns.length}`}>{visibleRuns.length === 0 ? <Empty>No runs match this Monitor.</Empty> : <div className="timeline-list">{visibleRuns.slice(0, 20).map((run) => { const identity = monitorRunPresentation(run, dashboardItems); const observations = listOf<Dict>(run, "observations").filter((observation) => String(observation.monitor_id ?? "") === selectedMonitorId); const warningCodes = listOf<string>(run, "warning_codes"); const errorCodes = listOf<string>(run, "error_codes"); return <article className="monitor-run-detail-row" key={String(run.run_id)}><i className={`timeline-dot ${String(run.status ?? "").toLowerCase()}`} /><div className="run-identity">{identity.targets.length === 1 ? <Link className="monitor-run-link" href={`#${monitorAnchorId(identity.targets[0].monitorId)}`} onClick={() => selectMonitor(identity.targets[0].monitorId)}>{identity.symbolLabel} · {identity.nameLabel}</Link> : <strong>{identity.symbolLabel} · {identity.nameLabel}</strong>}<span>{String(run.cadence ?? "MANUAL")} · {formatDate(run.completed_at)} · {String(run.rules_evaluated ?? 0)} rules</span><details className="run-error-drilldown" onToggle={(event) => { const runId = String(run.run_id ?? ""); if (event.currentTarget.open) setSelectedRunId(runId || null); else setSelectedRunId((current) => current === runId ? null : current); }}><summary>Run Receipt · {String(run.run_id)}</summary><div className="run-code-groups">{warningCodes.length > 0 && <div><strong>Warnings</strong><span>{warningCodes.join(" · ")}</span></div>}{errorCodes.length > 0 && <div><strong>Errors</strong><span>{errorCodes.join(" · ")}</span></div>}{warningCodes.length === 0 && errorCodes.length === 0 && <span>No run-level warning or error codes.</span>}</div><div className="run-observations">{observations.map((observation) => <div key={`${String(observation.monitor_id)}-${String(observation.rule_code)}`}><header><strong>{String(observation.rule_code)}</strong><Badge value={String(observation.state ?? "—")} /></header><span>{String(observation.message ?? "")}</span><small>Fact {formatDate(observation.fact_as_of)} · observed {String(observation.observed_value ?? "N/A")} · threshold {String(observation.threshold_value ?? "N/A")}</small>{listOf<string>(observation, "warning_codes").length > 0 && <code>{listOf<string>(observation, "warning_codes").join(" · ")}</code>}{listOf<string>(observation, "error_codes").length > 0 && <code className="text-red">{listOf<string>(observation, "error_codes").join(" · ")}</code>}{listOf<Dict>(observation, "diagnostics").map((diagnostic, index) => <section className="provider-diagnostic" key={`${String(diagnostic.provider)}-${String(diagnostic.stage)}-${index}`}><header><strong>{String(diagnostic.provider).toUpperCase()}</strong><span>{diagnosticStage(diagnostic.stage)}</span></header><dl><div><dt>Failure</dt><dd>{String(diagnostic.error_code)}</dd></div><div><dt>Type</dt><dd>{String(diagnostic.error_type ?? "unknown")}</dd></div><div><dt>Status</dt><dd>{diagnosticStatus(diagnostic)}</dd></div><div><dt>Attempts</dt><dd>{String(diagnostic.attempt_count)}</dd></div><div><dt>Retryable</dt><dd>{diagnostic.retryable ? "Yes" : "No"}</dd></div></dl></section>)}</div>)}</div></details></div><Badge value={String(run.status ?? "—")} /></article>; })}</div>}</Card></section>

                  <section id="monitor-panel-events" className="monitor-module-panel" role="tabpanel" aria-labelledby="monitor-tab-events" hidden={activeModule !== "events"}><Card kicker="STATE TRANSITIONS" title={`Event Stream · ${visibleEvents.length}`}>{visibleEvents.length === 0 ? <Empty>No transition events match this Monitor.</Empty> : <div className="timeline-list">{visibleEvents.slice(0, 20).map((event) => <article key={String(event.event_id)}><i className={`timeline-dot ${String(event.event_type ?? "").toLowerCase()}`} /><div className="event-copy"><strong>{String(event.rule_code ?? "Monitor event")}</strong><span>{formatDate(event.created_at)} · {String(event.severity ?? "—")} · {String(event.observed_value ?? "N/A")} / {String(event.threshold_value ?? "N/A")}</span><small>{String(event.message ?? "")}</small>{event.latest_resolution ? <small className="event-resolution">Handled: {String((event.latest_resolution as Dict).action ?? "—")} · {String((event.latest_resolution as Dict).note ?? "")}</small> : resolutionDraft?.eventId === String(event.event_id) ? <div className="event-resolution-editor"><label><span><b className="required-mark" aria-hidden="true">*</b>Resolution Note</span><input required autoFocus value={resolutionDraft.note} onChange={(change) => setResolutionDraft({ ...resolutionDraft, note: change.target.value })} placeholder="Record the judgment, follow-up action, or resolution reason" /></label><div><ActionButton onClick={submitResolution} busy={resolvingEvent}>{resolutionDraft.action === "RESOLVE" ? "Confirm Resolution" : "Confirm Acknowledgement"}</ActionButton><button type="button" onClick={() => setResolutionDraft(null)}>Cancel</button></div></div> : <div className="event-actions"><button type="button" onClick={() => beginResolution(String(event.event_id), "ACKNOWLEDGE")}>Acknowledge</button><button type="button" onClick={() => beginResolution(String(event.event_id), "RESOLVE")}>Resolve</button></div>}</div><Badge value={String(event.event_type ?? "—")} /></article>)}</div>}</Card></section>
                </div>}
                back={<MonitorEditor embedded initialMonitor={selectedDefinition} onClose={() => setEditingMonitor(undefined)} onSaved={(saved) => { setRunReceipt(saved); setEditingMonitor(undefined); result.refresh(); }} />}
              />
            </section>}
          </main>
        </div>
        <ConfirmationDialog
          open={confirmation !== null}
          title={confirmation?.title ?? "Confirm Monitor Action"}
          description={confirmation?.description}
          confirmLabel={confirmation?.confirmLabel}
          tone={confirmation?.tone}
          busy={running || archivingId !== null || lifecycleId !== null || resolvingEvent}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => confirmation?.onConfirm()}
        />
      </DataBoundary>
    </ConsoleShell>
  );
}
