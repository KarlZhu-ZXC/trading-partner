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
  if (ruleType === "PRICE_ABOVE") return `高于 $${String(rule.price_threshold ?? "—")}`;
  if (ruleType === "PRICE_BELOW") return `低于 $${String(rule.price_threshold ?? "—")}`;
  if (ruleType === "RISK_OVERALL_AT_LEAST") return `组合风险至少 ${String(rule.risk_status_threshold ?? "—")}`;
  const comparator = { GT: ">", GTE: "≥", LT: "<", LTE: "≤", EQ: "=", OCCURRED: "已发生" }[String(rule.comparator ?? "")] ?? String(rule.comparator ?? "—");
  const threshold = rule.comparator === "OCCURRED" ? "" : ` ${String(rule.numeric_threshold ?? "—")}`;
  const interval = rule.fact_type === "TECHNICAL" ? ` · ${String(rule.technical_interval ?? "1d")}` : "";
  const recovery = rule.recovery_threshold === null || rule.recovery_threshold === undefined ? "" : ` · 恢复 ${String(rule.recovery_threshold)}`;
  return `${String(rule.fact_type ?? "事实")} · ${String(rule.metric_key ?? "—")}${interval} ${comparator}${threshold}${recovery}`;
}

function diagnosticStage(value: unknown): string {
  return {
    weekend_quote_request: "周末代理行情请求",
    weekend_quote: "周末代理行情",
  }[String(value ?? "")] ?? String(value ?? "Provider 请求");
}

function diagnosticStatus(diagnostic: Dict): string {
  if (diagnostic.status_code !== null && diagnostic.status_code !== undefined) {
    return `HTTP ${String(diagnostic.status_code)}`;
  }
  if (diagnostic.status_class) return `HTTP ${String(diagnostic.status_class)}`;
  return "无 HTTP 响应";
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
    if (!window.confirm("将评估当前到期的 Monitor，并可能创建事件及发送已配置的通知。确认继续？")) return;
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
      setRunError(error instanceof Error ? error.message : "运行失败");
    } finally {
      setRunning(false);
    }
  }

  async function archiveMonitor(monitor: Dict) {
    const monitorId = String(monitor.monitor_id ?? "");
    const name = String(monitor.name ?? "未命名 Monitor");
    if (!monitorId || !window.confirm(`确认归档「${name}」？\n\n系统会追加一个 ARCHIVED 版本；历史版本、运行记录和事件不会被删除。`)) return;
    setArchivingId(monitorId);
    setRunError(null);
    try {
      const response = await postApi<Dict>(`/api/monitors/${encodeURIComponent(monitorId)}/archive`, {
        expected_version: Number(monitor.version),
        confirmation: "monitor_archive",
      });
      if (response.ok === false) {
        const first = Array.isArray(response.errors) ? response.errors[0] as Dict | undefined : undefined;
        throw new Error(String(first?.message ?? "Monitor 归档失败"));
      }
      if (String(editingMonitor?.monitor_id ?? "") === monitorId) setEditingMonitor(undefined);
      setRunReceipt(response);
      await result.refresh();
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Monitor 归档失败");
    } finally {
      setArchivingId(null);
    }
  }

  async function changeMonitorStatus(monitor: Dict, status: "ACTIVE" | "PAUSED") {
    const monitorId = String(monitor.monitor_id ?? "");
    const name = String(monitor.name ?? "未命名 Monitor");
    const action = status === "ACTIVE"
      ? String(monitor.status ?? "").toUpperCase() === "ARCHIVED" ? "恢复并激活" : "重新激活"
      : "暂停";
    if (!monitorId || !window.confirm(`确认${action}「${name}」？\n\n系统会追加新的 ${status} 版本并保留全部历史。`)) return;
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
        throw new Error(displayJson(envelope.errors ?? `Monitor ${action}失败`));
      }
      setRunReceipt(response);
      await result.refresh();
    } catch (error) {
      setRunError(error instanceof Error ? error.message : `Monitor ${action}失败`);
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
      setResolutionError("请填写处理说明。说明会进入不可变审计记录。");
      return;
    }
    const actionLabel = resolutionDraft.action === "RESOLVE" ? "标记为已解决" : "确认已知";
    if (!window.confirm(`确认将该事件${actionLabel}？`)) return;
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
      setResolutionError(error instanceof Error ? error.message : "事件处理失败");
    } finally {
      setResolvingEvent(false);
    }
  }

  return (
    <ConsoleShell active="monitors" eyebrow="Deterministic monitoring" title="Monitor 运行与事件">
      <DataBoundary loading={result.loading} error={result.error}>
        <div className="toolbar">
          <p>展示持久化定义、每条规则的最新状态、不可变运行观测与状态转换事件。</p>
          <div className="toolbar-actions"><ActionButton onClick={() => setEditingMonitor(null)}>新建 Monitor</ActionButton><ActionButton onClick={runDue} busy={running}>运行到期 Monitor</ActionButton><RefreshButton onClick={result.refresh} loading={result.loading} /></div>
        </div>
        {runError && <div className="inline-error">{runError}</div>}
        {resolutionError && <div className="inline-error" role="alert">{resolutionError}</div>}
        {runReceipt !== null && <details className="run-receipt"><summary>查看本次运行回执</summary><pre>{displayJson(runReceipt)}</pre></details>}
        {editingMonitor === null && <MonitorEditor onClose={() => setEditingMonitor(undefined)} onSaved={(saved) => { setRunReceipt(saved); setEditingMonitor(undefined); result.refresh(); }} />}
        <Card
          className="monitor-list-panel"
          kicker="MONITOR DEFINITIONS"
          title="Monitor 列表"
          action={(
            <div className="monitor-header-tools">
              <label className="monitor-status-filter">
                <span>状态</span>
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as (typeof MONITOR_STATUSES)[number])} aria-label="按 Monitor 状态筛选">
                  {MONITOR_STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}
                </select>
              </label>
              <label className="monitor-search-box">
                <span>标的筛选</span>
                <input
                  type="search"
                  value={instrumentFilter}
                  onChange={(event) => setInstrumentFilter(event.target.value)}
                  placeholder="TTWO、TSLA、equity:US:TTWO"
                  aria-label="按标的代码筛选 Monitor"
                />
              </label>
              <span className="monitor-filter-result" aria-live="polite">{filteredItems.length} / {items.length}</span>
            </div>
          )}
        >
          <div className="monitor-list">
          {items.length === 0 ? <Empty>尚无 Monitor 定义。</Empty> : filteredItems.length === 0 ? <Empty>没有匹配该标的的 Monitor。</Empty> : filteredItems.map((item) => {
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
                    <h2>{String(monitor.name ?? "未命名 Monitor")}</h2>
                    <span className="mono">{String(monitor.monitor_id)} · v{String(monitor.version ?? "—")}</span>
                  </div>
                  <div className="monitor-title-actions">
                    <button type="button" className={selectedMonitorId === String(monitor.monitor_id) ? "selected" : ""} onClick={() => setSelectedMonitorId((current) => current === String(monitor.monitor_id) ? null : String(monitor.monitor_id))}>{selectedMonitorId === String(monitor.monitor_id) ? "关闭详情" : "运行详情"}</button>
                    <button type="button" onClick={() => setEditingMonitor(monitor)}>编辑</button>
                    {String(monitor.status ?? "").toUpperCase() === "ACTIVE" ? <button type="button" disabled={lifecycleId === String(monitor.monitor_id)} onClick={() => { void changeMonitorStatus(monitor, "PAUSED"); }}>{lifecycleId === String(monitor.monitor_id) ? "处理中…" : "暂停"}</button> : <button className="restore-text" type="button" disabled={lifecycleId === String(monitor.monitor_id)} onClick={() => { void changeMonitorStatus(monitor, "ACTIVE"); }}>{lifecycleId === String(monitor.monitor_id) ? "处理中…" : String(monitor.status ?? "").toUpperCase() === "ARCHIVED" ? "恢复并激活" : "激活"}</button>}
                    {String(monitor.status ?? "").toUpperCase() !== "ARCHIVED" && <button className="monitor-delete-button" type="button" disabled={archivingId === String(monitor.monitor_id)} onClick={() => { void archiveMonitor(monitor); }}>{archivingId === String(monitor.monitor_id) ? "归档中…" : "归档"}</button>}
                    <Badge value={String(monitor.status ?? "—")} />
                  </div>
                </div>
                <div className="monitor-runtime-strip">
                  {priceObservation && (
                    <section className={`monitor-price-observation ${priceObservation.kind}`} aria-label="最近运行价格">
                      <div className="monitor-price-value">
                        <span>最新价</span>
                        <strong>{priceObservation.kind === "available" ? formatDecimal(priceObservation.value, 4) : "—"}</strong>
                      </div>
                      <div className="monitor-price-time">
                        <span>{priceObservation.kind === "mixed" ? "观测状态" : "事实时间"}</span>
                        <strong>{priceObservation.kind === "mixed" ? "存在多个价格观测" : formatDate(priceObservation.factAsOf)}</strong>
                        <small>
                          {priceObservation.kind === "unavailable"
                            ? "未取得可用价格"
                            : priceObservation.kind === "mixed"
                              ? "请查看各价格规则"
                              : priceObservation.extendedHours
                                ? "盘前/盘后"
                                : "收盘观测"}
                        </small>
                      </div>
                    </section>
                  )}
                  <div className="monitor-facts">
                    <span>Cadence <strong>{String(monitor.cadence ?? "—")}</strong></span>
                    <span>创建 <strong>{formatDate(item.monitor_created_at ?? monitor.created_at)}</strong></span>
                    <span>最近编辑 <strong>{formatDate(item.monitor_updated_at ?? monitor.created_at)}</strong></span>
                    <span>最近运行 <strong>{formatDate(latest.completed_at)}</strong></span>
                    <span>规则 <strong>{rules.length}</strong></span>
                    <span>事件 <strong>{String(latest.events_created ?? 0)}</strong></span>
                  </div>
                </div>
                {latestJudgment.judgment_id && <section className={`monitor-judgment-card ${String(latestJudgment.urgency ?? "watch").toLowerCase()}`}>
                  <header><strong>LLM 复合判断 · {String(latestJudgment.conclusion ?? latestJudgment.status ?? "—")}</strong><Badge value={String(latestJudgment.urgency ?? latestJudgment.status ?? "—")} /></header>
                  <p>{String(latestJudgment.market_state ?? latestJudgment.summary ?? "")}</p>
                  <div><span>阶段 {String(latestJudgment.phase ?? "—")}</span><span>背离 {String(latestJudgment.divergence ?? "—")}</span><span>数量 {String(latestJudgment.quantity_min ?? 0)}–{String(latestJudgment.quantity_max ?? 0)}</span><span>{String(latestJudgment.provider ?? "—")} / {String(latestJudgment.model ?? "—")}</span></div>
                  <small>下一触发：{String(latestJudgment.next_trigger ?? "—")} · 失效：{String(latestJudgment.invalidation ?? "—")}</small>
                  {Boolean(latestJudgment.web_search_used) && <details><summary>联网搜索来源 · {judgmentWebSources.length}</summary><div>{judgmentWebSources.map((url) => <a href={url} key={url} rel="noreferrer" target="_blank">{url}</a>)}</div></details>}
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
                        <strong className="rule-description">{String(rule.description ?? "旧版本未填写具体释义")}</strong>
                        <small className="rule-condition">{ruleCondition(rule)}</small>
                        {showIndividualObservation && <span className="rule-observed">当前 {String(state.observed_value ?? "N/A")}</span>}
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
          <div><p className="card-kicker">RUN & EVENT DRILL-DOWN</p><h2>{selectedMonitor ? `${shortId(((selectedMonitor.monitor ?? {}) as Dict).primary_instrument_id)} · ${String(((selectedMonitor.monitor ?? {}) as Dict).name ?? "Monitor")}` : "全部 Monitor"}</h2></div>
          {selectedMonitorId && <button className="close-button" type="button" onClick={() => setSelectedMonitorId(null)}>清除筛选</button>}
        </div>
        <div className="two-column monitor-drilldown">
          <Card kicker="IMMUTABLE OBSERVATIONS" title={`最近运行 · ${visibleRuns.length}`}>
            {visibleRuns.length === 0 ? <Empty>当前筛选没有运行。</Empty> : (
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
                        <details className="run-error-drilldown"><summary>Run receipt · {String(run.run_id)}</summary><div className="run-code-groups">{warningCodes.length > 0 && <div><strong>Warnings</strong><span>{warningCodes.join(" · ")}</span></div>}{errorCodes.length > 0 && <div><strong>Errors</strong><span>{errorCodes.join(" · ")}</span></div>}{warningCodes.length === 0 && errorCodes.length === 0 && <span>无 run-level warning/error code。</span>}</div><div className="run-observations">{observations.map((observation) => <div key={`${String(observation.monitor_id)}-${String(observation.rule_code)}`}><header><strong>{String(observation.rule_code)}</strong><Badge value={String(observation.state ?? "—")} /></header><span>{String(observation.message ?? "")}</span><small>事实 {formatDate(observation.fact_as_of)} · observed {String(observation.observed_value ?? "N/A")} · threshold {String(observation.threshold_value ?? "N/A")}</small>{listOf<string>(observation, "warning_codes").length > 0 && <code>{listOf<string>(observation, "warning_codes").join(" · ")}</code>}{listOf<string>(observation, "error_codes").length > 0 && <code className="text-red">{listOf<string>(observation, "error_codes").join(" · ")}</code>}{listOf<Dict>(observation, "diagnostics").map((diagnostic, index) => <section className="provider-diagnostic" key={`${String(diagnostic.provider)}-${String(diagnostic.stage)}-${index}`}><header><strong>{String(diagnostic.provider).toUpperCase()}</strong><span>{diagnosticStage(diagnostic.stage)}</span></header><dl><div><dt>失败</dt><dd>{String(diagnostic.error_code)}</dd></div><div><dt>类型</dt><dd>{String(diagnostic.error_type ?? "unknown")}</dd></div><div><dt>状态</dt><dd>{diagnosticStatus(diagnostic)}</dd></div><div><dt>尝试</dt><dd>{String(diagnostic.attempt_count)} 次</dd></div><div><dt>可重试</dt><dd>{diagnostic.retryable ? "是" : "否"}</dd></div></dl></section>)}</div>)}</div></details>
                      </div>
                      <Badge value={String(run.status ?? "—")} />
                    </article>
                  );
                })}
              </div>
            )}
          </Card>
          <Card kicker="STATE TRANSITIONS" title={`事件流 · ${visibleEvents.length}`}>
            {visibleEvents.length === 0 ? <Empty>当前筛选没有状态转换事件。</Empty> : (
              <div className="timeline-list">
                {visibleEvents.slice(0, 20).map((event) => (
                  <article key={String(event.event_id)}>
                    <i className={`timeline-dot ${String(event.event_type ?? "").toLowerCase()}`} />
                    <div className="event-copy">
                      <strong>{String(event.rule_code ?? "Monitor event")}</strong>
                      <span>{formatDate(event.created_at)} · {String(event.severity ?? "—")} · {String(event.observed_value ?? "N/A")} / {String(event.threshold_value ?? "N/A")}</span>
                      <small>{String(event.message ?? "")}</small>
                      {event.latest_resolution ? (
                        <small className="event-resolution">已处理：{String((event.latest_resolution as Dict).action ?? "—")} · {String((event.latest_resolution as Dict).note ?? "")}</small>
                      ) : resolutionDraft?.eventId === String(event.event_id) ? (
                        <div className="event-resolution-editor">
                          <label><span>处理说明</span><input autoFocus value={resolutionDraft.note} onChange={(change) => setResolutionDraft({ ...resolutionDraft, note: change.target.value })} placeholder="记录判断、后续动作或解决原因" /></label>
                          <div><ActionButton onClick={submitResolution} busy={resolvingEvent}>{resolutionDraft.action === "RESOLVE" ? "确认解决" : "确认已知"}</ActionButton><button type="button" onClick={() => setResolutionDraft(null)}>取消</button></div>
                        </div>
                      ) : (
                        <div className="event-actions"><button type="button" onClick={() => beginResolution(String(event.event_id), "ACKNOWLEDGE")}>确认已知</button><button type="button" onClick={() => beginResolution(String(event.event_id), "RESOLVE")}>标记解决</button></div>
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
