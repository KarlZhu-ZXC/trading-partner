"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ConsoleShell } from "../components/console-shell";
import { ActionButton, Badge, Card, DataBoundary, Empty, RefreshButton, displayJson, formatDate, formatDecimal, monitorAnchorId, shortId } from "../components/ui";
import { envelopeData, listOf, postApi, useApi } from "../lib/api";
import { monitorRunPresentation } from "../lib/monitor-runs";
import { MonitorEditor } from "./monitor-editor";

type Dict = Record<string, unknown>;
type MonitorPriceObservation = {
  kind: "available" | "unavailable" | "mixed";
  value: unknown;
  factAsOf: unknown;
  extendedHours: boolean;
};

const MONITOR_STATUSES = ["ALL", "ACTIVE", "PAUSED", "ARCHIVED"] as const;

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
  const [archivingId, setArchivingId] = useState<string | null>(null);
  const [lifecycleId, setLifecycleId] = useState<string | null>(null);
  const [resolutionDraft, setResolutionDraft] = useState<{ eventId: string; action: "ACKNOWLEDGE" | "RESOLVE"; note: string; idempotencyKey: string } | null>(null);
  const [resolvingEvent, setResolvingEvent] = useState(false);
  const [resolutionError, setResolutionError] = useState<string | null>(null);
  const [instrumentFilter, setInstrumentFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<(typeof MONITOR_STATUSES)[number]>("ACTIVE");
  const [selectedMonitorId, setSelectedMonitorId] = useState<string | null>(null);
  const dashboard = envelopeData<Dict>((result.data?.dashboard as Dict | undefined));
  const runs = envelopeData<Dict>((result.data?.runs as Dict | undefined));
  const events = envelopeData<Dict>((result.data?.events as Dict | undefined));
  const dashboardItems = listOf<Dict>(dashboard, "items");
  const items = dashboardItems.filter((item) => {
    const monitor = (item.monitor ?? {}) as Dict;
    return statusFilter === "ALL" || String(monitor.status ?? "").toUpperCase() === statusFilter;
  });
  const normalizedInstrumentFilter = instrumentFilter.trim().toLocaleLowerCase();
  const filteredItems = items.filter((item) => monitorMatchesInstrument(item, normalizedInstrumentFilter));
  const runItems = listOf<Dict>(runs, "runs");
  const eventItems = listOf<Dict>(events, "events");
  const selectedMonitor = items.find((item) => String(((item.monitor ?? {}) as Dict).monitor_id ?? "") === selectedMonitorId) ?? null;
  const visibleRuns = selectedMonitorId ? runItems.filter((run) => [
    ...listOf<string>(run, "requested_monitor_ids"),
    ...listOf<string>(run, "selected_monitor_ids"),
    ...listOf<Dict>(run, "observations").map((observation) => String(observation.monitor_id ?? "")),
  ].includes(selectedMonitorId)) : runItems;
  const visibleEvents = selectedMonitorId ? eventItems.filter((event) => String(event.monitor_id ?? "") === selectedMonitorId) : eventItems;

  useEffect(() => {
    if (items.length === 0 || !window.location.hash) return;
    const targetId = decodeURIComponent(window.location.hash.slice(1));
    const target = document.getElementById(targetId);
    if (!target) return;
    window.scrollTo({
      top: target.getBoundingClientRect().top + window.scrollY - 24,
      behavior: "smooth",
    });
  }, [items.length]);

  async function runDue() {
    if (!window.confirm("Evaluate currently due Monitors? This may create events and send configured notifications.")) return;
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

  async function archiveMonitor(monitor: Dict) {
    const monitorId = String(monitor.monitor_id ?? "");
    const name = String(monitor.name ?? "Untitled Monitor");
    if (!monitorId || !window.confirm(`Archive “${name}”?\n\nAn ARCHIVED version will be appended. Historical versions, runs, and events will not be deleted.`)) return;
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

  async function changeMonitorStatus(monitor: Dict, status: "ACTIVE" | "PAUSED") {
    const monitorId = String(monitor.monitor_id ?? "");
    const name = String(monitor.name ?? "Untitled Monitor");
    const action = status === "ACTIVE"
      ? String(monitor.status ?? "").toUpperCase() === "ARCHIVED" ? "restore and activate" : "reactivate"
      : "pause";
    if (!monitorId || !window.confirm(`Confirm ${action} for “${name}”?\n\nA new ${status} version will be appended and all history will be preserved.`)) return;
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

  function beginResolution(eventId: string, action: "ACKNOWLEDGE" | "RESOLVE") {
    setResolutionError(null);
    setResolutionDraft({
      eventId,
      action,
      note: "",
      idempotencyKey: `console-event-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    });
  }

  async function submitResolution() {
    if (!resolutionDraft) return;
    const note = resolutionDraft.note.trim();
    if (!note) {
      setResolutionError("Enter a resolution note. It will become part of the immutable audit record.");
      return;
    }
    const actionLabel = resolutionDraft.action === "RESOLVE" ? "resolved" : "acknowledged";
    if (!window.confirm(`Mark this event as ${actionLabel}?`)) return;
    setResolvingEvent(true);
    setResolutionError(null);
    try {
      const receipt = await postApi<unknown>("/api/tools/invoke", {
        tool_name: "monitor_manage",
        arguments: {
          request: {
            operation: "resolve_event",
            event_id: resolutionDraft.eventId,
            action: resolutionDraft.action,
            note,
            confirmed_by: "user",
            idempotency_key: resolutionDraft.idempotencyKey,
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

  return (
    <ConsoleShell active="monitors" eyebrow="Deterministic monitoring" title="Monitor Runs & Events">
      <DataBoundary loading={result.loading} error={result.error}>
        <div className="toolbar">
          <p>Inspect durable definitions, the latest state of every rule, immutable run observations, and transition events.</p>
          <div className="toolbar-actions"><ActionButton onClick={() => setEditingMonitor(null)}>New Monitor</ActionButton><ActionButton onClick={runDue} busy={running}>Run Due Monitors</ActionButton><RefreshButton onClick={result.refresh} loading={result.loading} /></div>
        </div>
        {runError && <div className="inline-error">{runError}</div>}
        {resolutionError && <div className="inline-error" role="alert">{resolutionError}</div>}
        {runReceipt !== null && <details className="run-receipt"><summary>View run receipt</summary><pre>{displayJson(runReceipt)}</pre></details>}
        {editingMonitor === null && <MonitorEditor onClose={() => setEditingMonitor(undefined)} onSaved={(saved) => { setRunReceipt(saved); setEditingMonitor(undefined); result.refresh(); }} />}
        <Card
          className="monitor-list-panel"
          kicker="MONITOR DEFINITIONS"
          title="Monitor List"
          action={(
            <div className="monitor-header-tools">
              <label className="monitor-status-filter">
                <span>Status</span>
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as (typeof MONITOR_STATUSES)[number])} aria-label="Filter by Monitor status">
                  {MONITOR_STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}
                </select>
              </label>
              <label className="monitor-search-box">
                <span>Target Filter</span>
                <input
                  type="search"
                  value={instrumentFilter}
                  onChange={(event) => setInstrumentFilter(event.target.value)}
                  placeholder="TTWO, TSLA, equity:US:TTWO"
                  aria-label="Filter Monitors by target symbol"
                />
              </label>
              <span className="monitor-filter-result" aria-live="polite">{filteredItems.length} / {items.length}</span>
            </div>
          )}
        >
          <div className="monitor-list">
          {items.length === 0 ? <Empty>No Monitor definitions yet.</Empty> : filteredItems.length === 0 ? <Empty>No Monitors match this target.</Empty> : filteredItems.map((item) => {
            const monitor = (item.monitor ?? {}) as Dict;
            const rules = listOf<Dict>(monitor, "rules");
            const states = listOf<Dict>(item, "rule_states");
            const statesByCode = new Map(states.map((state) => [String(state.rule_code), state]));
            const latest = (item.latest_run ?? {}) as Dict;
            const latestJudgment = (item.latest_judgment ?? {}) as Dict;
            const judgmentWebSources = listOf<string>(latestJudgment, "web_source_urls");
            const priceObservation = monitorPriceObservation(monitor, rules, latest, states);
            const isEditing = editingMonitor !== null
              && editingMonitor !== undefined
              && String(editingMonitor.monitor_id) === String(monitor.monitor_id);
            return (
              <section className="monitor-card" id={monitorAnchorId(monitor.monitor_id)} key={String(monitor.monitor_id)}>
                <MonitorFlipSurface
                  flipped={isEditing}
                  front={(
                    <>
                <div className="monitor-title-row">
                  <div className="symbol-tile large">{shortId(monitor.primary_instrument_id)}</div>
                  <div className="monitor-copy">
                    <h2>{String(monitor.name ?? "Untitled Monitor")}</h2>
                    <span className="mono">{String(monitor.monitor_id)} · v{String(monitor.version ?? "—")}</span>
                  </div>
                  <div className="monitor-title-actions">
                    <button type="button" className={selectedMonitorId === String(monitor.monitor_id) ? "selected" : ""} onClick={() => setSelectedMonitorId((current) => current === String(monitor.monitor_id) ? null : String(monitor.monitor_id))}>{selectedMonitorId === String(monitor.monitor_id) ? "Close Details" : "Run Details"}</button>
                    <button type="button" onClick={() => setEditingMonitor(monitor)}>Edit</button>
                    {String(monitor.status ?? "").toUpperCase() === "ACTIVE" ? <button type="button" disabled={lifecycleId === String(monitor.monitor_id)} onClick={() => { void changeMonitorStatus(monitor, "PAUSED"); }}>{lifecycleId === String(monitor.monitor_id) ? "Working…" : "Pause"}</button> : <button className="restore-text" type="button" disabled={lifecycleId === String(monitor.monitor_id)} onClick={() => { void changeMonitorStatus(monitor, "ACTIVE"); }}>{lifecycleId === String(monitor.monitor_id) ? "Working…" : String(monitor.status ?? "").toUpperCase() === "ARCHIVED" ? "Restore & Activate" : "Activate"}</button>}
                    {String(monitor.status ?? "").toUpperCase() !== "ARCHIVED" && <button className="monitor-delete-button" type="button" disabled={archivingId === String(monitor.monitor_id)} onClick={() => { void archiveMonitor(monitor); }}>{archivingId === String(monitor.monitor_id) ? "Archiving…" : "Archive"}</button>}
                    <Badge value={String(monitor.status ?? "—")} />
                  </div>
                </div>
                <div className="monitor-runtime-strip">
                  {priceObservation && (
                    <section className={`monitor-price-observation ${priceObservation.kind}`} aria-label="Latest run price">
                      <div className="monitor-price-value">
                        <span>Latest Price</span>
                        <strong>{priceObservation.kind === "available" ? formatDecimal(priceObservation.value, 4) : "—"}</strong>
                      </div>
                      <div className="monitor-price-time">
                        <span>{priceObservation.kind === "mixed" ? "Observation State" : "Fact Time"}</span>
                        <strong>{priceObservation.kind === "mixed" ? "Multiple price observations" : formatDate(priceObservation.factAsOf)}</strong>
                        <small>
                          {priceObservation.kind === "unavailable"
                            ? "No usable price"
                            : priceObservation.kind === "mixed"
                              ? "Inspect individual price rules"
                              : priceObservation.extendedHours
                                ? "Pre/Post Market"
                                : "Close Observation"}
                        </small>
                      </div>
                    </section>
                  )}
                  <div className="monitor-facts">
                    <span>Cadence <strong>{String(monitor.cadence ?? "—")}</strong></span>
                    <span>Created <strong>{formatDate(item.monitor_created_at ?? monitor.created_at)}</strong></span>
                    <span>Last Edited <strong>{formatDate(item.monitor_updated_at ?? monitor.created_at)}</strong></span>
                    <span>Latest Run <strong>{formatDate(latest.completed_at)}</strong></span>
                    <span>Rules <strong>{rules.length}</strong></span>
                    <span>Events <strong>{String(latest.events_created ?? 0)}</strong></span>
                  </div>
                </div>
                {latestJudgment.judgment_id && <section className={`monitor-judgment-card ${String(latestJudgment.urgency ?? "watch").toLowerCase()}`}>
                  <header><strong>Composite LLM Judgment · {String(latestJudgment.conclusion ?? latestJudgment.status ?? "—")}</strong><Badge value={String(latestJudgment.urgency ?? latestJudgment.status ?? "—")} /></header>
                  <p>{String(latestJudgment.market_state ?? latestJudgment.summary ?? "")}</p>
                  <div><span>Phase {String(latestJudgment.phase ?? "—")}</span><span>Divergence {String(latestJudgment.divergence ?? "—")}</span><span>Quantity {String(latestJudgment.quantity_min ?? 0)}–{String(latestJudgment.quantity_max ?? 0)}</span><span>{String(latestJudgment.provider ?? "—")} / {String(latestJudgment.model ?? "—")}</span></div>
                  <small>Next trigger: {String(latestJudgment.next_trigger ?? "—")} · Invalidation: {String(latestJudgment.invalidation ?? "—")}</small>
                  {Boolean(latestJudgment.web_search_used) && <details><summary>Web Search Sources · {judgmentWebSources.length}</summary><div>{judgmentWebSources.map((url) => <a href={url} key={url} rel="noreferrer" target="_blank">{url}</a>)}</div></details>}
                </section>}
                <div className="rule-grid">
                  {rules.map((rule) => {
                    const state = statesByCode.get(String(rule.rule_code)) ?? { rule_code: rule.rule_code, state: "NOT_EVALUATED" };
                    const showIndividualObservation = !isPriceRule(rule) || priceObservation?.kind === "mixed";
                    return (
                      <article className={`rule-card ${String(state.state ?? "").toLowerCase()}`} key={String(state.rule_code)}>
                        <div className="rule-card-head">
                          <span className="mono">{String(state.rule_code)}</span>
                          <div className="rule-card-meta">
                            <span className={`rule-state ${String(state.state ?? "").toLowerCase()}`}>{String(state.state ?? "—")}</span>
                            <span className={`rule-severity ${String(rule.severity ?? "").toLowerCase()}`}>{String(rule.severity ?? "—")}</span>
                          </div>
                        </div>
                        <strong className="rule-description">{String(rule.description ?? "Legacy version has no human-readable meaning")}</strong>
                        <small className="rule-condition">{ruleCondition(rule)}</small>
                        {showIndividualObservation && <span className="rule-observed">Current {String(state.observed_value ?? "N/A")}</span>}
                        {showIndividualObservation && <time>{formatDate(state.fact_as_of)}</time>}
                      </article>
                    );
                  })}
                </div>
                    </>
                  )}
                  back={(
                    <MonitorEditor
                      embedded
                      initialMonitor={monitor}
                      onClose={() => setEditingMonitor(undefined)}
                      onSaved={(saved) => {
                        setRunReceipt(saved);
                        setEditingMonitor(undefined);
                        result.refresh();
                      }}
                    />
                  )}
                />
              </section>
            );
          })}
          </div>
        </Card>
        <div className="monitor-detail-heading">
          <div><p className="card-kicker">RUN & EVENT DRILL-DOWN</p><h2>{selectedMonitor ? `${shortId(((selectedMonitor.monitor ?? {}) as Dict).primary_instrument_id)} · ${String(((selectedMonitor.monitor ?? {}) as Dict).name ?? "Monitor")}` : "All Monitors"}</h2></div>
          {selectedMonitorId && <button className="close-button" type="button" onClick={() => setSelectedMonitorId(null)}>Clear Filter</button>}
        </div>
        <div className="two-column monitor-drilldown">
          <Card kicker="IMMUTABLE OBSERVATIONS" title={`Recent Runs · ${visibleRuns.length}`}>
            {visibleRuns.length === 0 ? <Empty>No runs match the current filter.</Empty> : (
              <div className="timeline-list">
                {visibleRuns.slice(0, 20).map((run) => {
                    const identity = monitorRunPresentation(run, dashboardItems);
                    const observations = listOf<Dict>(run, "observations").filter((observation) => !selectedMonitorId || String(observation.monitor_id ?? "") === selectedMonitorId);
                    const warningCodes = listOf<string>(run, "warning_codes");
                    const errorCodes = listOf<string>(run, "error_codes");
                  return (
                    <article className="monitor-run-detail-row" key={String(run.run_id)}>
                      <i className={`timeline-dot ${String(run.status ?? "").toLowerCase()}`} />
                      <div className="run-identity">
                        {identity.targets.length === 1 ? <Link className="monitor-run-link" href={`#${monitorAnchorId(identity.targets[0].monitorId)}`}>{identity.symbolLabel} · {identity.nameLabel}</Link> : <strong>{identity.symbolLabel} · {identity.nameLabel}</strong>}
                        <span>{String(run.cadence ?? "MANUAL")} · {formatDate(run.completed_at)} · {String(run.rules_evaluated ?? 0)} rules</span>
                        <details className="run-error-drilldown"><summary>Run receipt · {String(run.run_id)}</summary><div className="run-code-groups">{warningCodes.length > 0 && <div><strong>Warnings</strong><span>{warningCodes.join(" · ")}</span></div>}{errorCodes.length > 0 && <div><strong>Errors</strong><span>{errorCodes.join(" · ")}</span></div>}{warningCodes.length === 0 && errorCodes.length === 0 && <span>No run-level warning or error codes.</span>}</div><div className="run-observations">{observations.map((observation) => <div key={`${String(observation.monitor_id)}-${String(observation.rule_code)}`}><header><strong>{String(observation.rule_code)}</strong><Badge value={String(observation.state ?? "—")} /></header><span>{String(observation.message ?? "")}</span><small>Fact {formatDate(observation.fact_as_of)} · observed {String(observation.observed_value ?? "N/A")} · threshold {String(observation.threshold_value ?? "N/A")}</small>{listOf<string>(observation, "warning_codes").length > 0 && <code>{listOf<string>(observation, "warning_codes").join(" · ")}</code>}{listOf<string>(observation, "error_codes").length > 0 && <code className="text-red">{listOf<string>(observation, "error_codes").join(" · ")}</code>}{listOf<Dict>(observation, "diagnostics").map((diagnostic, index) => <section className="provider-diagnostic" key={`${String(diagnostic.provider)}-${String(diagnostic.stage)}-${index}`}><header><strong>{String(diagnostic.provider).toUpperCase()}</strong><span>{diagnosticStage(diagnostic.stage)}</span></header><dl><div><dt>Failure</dt><dd>{String(diagnostic.error_code)}</dd></div><div><dt>Type</dt><dd>{String(diagnostic.error_type ?? "unknown")}</dd></div><div><dt>Status</dt><dd>{diagnosticStatus(diagnostic)}</dd></div><div><dt>Attempts</dt><dd>{String(diagnostic.attempt_count)}</dd></div><div><dt>Retryable</dt><dd>{diagnostic.retryable ? "Yes" : "No"}</dd></div></dl></section>)}</div>)}</div></details>
                      </div>
                      <Badge value={String(run.status ?? "—")} />
                    </article>
                  );
                })}
              </div>
            )}
          </Card>
          <Card kicker="STATE TRANSITIONS" title={`Event Stream · ${visibleEvents.length}`}>
            {visibleEvents.length === 0 ? <Empty>No transition events match the current filter.</Empty> : (
              <div className="timeline-list">
                {visibleEvents.slice(0, 20).map((event) => (
                  <article key={String(event.event_id)}>
                    <i className={`timeline-dot ${String(event.event_type ?? "").toLowerCase()}`} />
                    <div className="event-copy">
                      <strong>{String(event.rule_code ?? "Monitor event")}</strong>
                      <span>{formatDate(event.created_at)} · {String(event.severity ?? "—")} · {String(event.observed_value ?? "N/A")} / {String(event.threshold_value ?? "N/A")}</span>
                      <small>{String(event.message ?? "")}</small>
                      {event.latest_resolution ? (
                        <small className="event-resolution">Handled: {String((event.latest_resolution as Dict).action ?? "—")} · {String((event.latest_resolution as Dict).note ?? "")}</small>
                      ) : resolutionDraft?.eventId === String(event.event_id) ? (
                        <div className="event-resolution-editor">
                          <label><span>Resolution Note</span><input autoFocus value={resolutionDraft.note} onChange={(change) => setResolutionDraft({ ...resolutionDraft, note: change.target.value })} placeholder="Record the judgment, follow-up action, or resolution reason" /></label>
                          <div><ActionButton onClick={submitResolution} busy={resolvingEvent}>{resolutionDraft.action === "RESOLVE" ? "Confirm Resolution" : "Confirm Acknowledgement"}</ActionButton><button type="button" onClick={() => setResolutionDraft(null)}>Cancel</button></div>
                        </div>
                      ) : (
                        <div className="event-actions"><button type="button" onClick={() => beginResolution(String(event.event_id), "ACKNOWLEDGE")}>Acknowledge</button><button type="button" onClick={() => beginResolution(String(event.event_id), "RESOLVE")}>Resolve</button></div>
                      )}
                    </div>
                    <Badge value={String(event.event_type ?? "—")} />
                  </article>
                ))}
              </div>
            )}
          </Card>
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
