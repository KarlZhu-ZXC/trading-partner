"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
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
  return `${String(rule.fact_type ?? "事实")} · ${String(rule.metric_key ?? "—")} ${comparator}${threshold}`;
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
  const [resolutionDraft, setResolutionDraft] = useState<{ eventId: string; action: "ACKNOWLEDGE" | "RESOLVE"; note: string; idempotencyKey: string } | null>(null);
  const [resolvingEvent, setResolvingEvent] = useState(false);
  const [resolutionError, setResolutionError] = useState<string | null>(null);
  const [instrumentFilter, setInstrumentFilter] = useState("");
  const dashboard = envelopeData<Dict>((result.data?.dashboard as Dict | undefined));
  const runs = envelopeData<Dict>((result.data?.runs as Dict | undefined));
  const events = envelopeData<Dict>((result.data?.events as Dict | undefined));
  const items = listOf<Dict>(dashboard, "items");
  const normalizedInstrumentFilter = instrumentFilter.trim().toLocaleLowerCase();
  const filteredItems = items.filter((item) => monitorMatchesInstrument(item, normalizedInstrumentFilter));
  const runItems = listOf<Dict>(runs, "runs");
  const eventItems = listOf<Dict>(events, "events");

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
                  <div className="monitor-title-actions"><button type="button" onClick={() => setEditingMonitor(monitor)}>编辑</button><Badge value={String(monitor.status ?? "—")} /></div>
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
        <div className="two-column">
          <Card kicker="IMMUTABLE OBSERVATIONS" title="最近运行">
            {runItems.length === 0 ? <Empty>暂无运行。</Empty> : (
              <div className="timeline-list">
                {runItems.slice(0, 12).map((run) => {
                  const identity = monitorRunPresentation(run, items);
                  return (
                    <article key={String(run.run_id)}>
                      <i className={`timeline-dot ${String(run.status ?? "").toLowerCase()}`} />
                      <div className="run-identity">
                        <strong>{identity.symbolLabel} · {identity.nameLabel}</strong>
                        <span>{String(run.cadence ?? "MANUAL")} · {formatDate(run.completed_at)} · {String(run.rules_evaluated ?? 0)} rules</span>
                      </div>
                      <Badge value={String(run.status ?? "—")} />
                    </article>
                  );
                })}
              </div>
            )}
          </Card>
          <Card kicker="STATE TRANSITIONS" title="事件流">
            {eventItems.length === 0 ? <Empty>当前没有状态转换事件。</Empty> : (
              <div className="timeline-list">
                {eventItems.slice(0, 12).map((event) => (
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
