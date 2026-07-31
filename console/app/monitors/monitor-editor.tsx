"use client";

import { useState } from "react";
import { ActionButton, Badge, displayJson } from "../components/ui";
import { postApi } from "../lib/api";

type Dict = Record<string, unknown>;
type RuleType = "PRICE_ABOVE" | "PRICE_BELOW" | "RISK_OVERALL_AT_LEAST" | "FACT_COMPARISON";

type RuleDraft = {
  rule_code: string;
  description: string;
  rule_type: RuleType;
  severity: "INFO" | "MEDIUM" | "HIGH";
  instrument_id: string;
  price_threshold: string;
  risk_status_threshold: "PASS" | "WARN" | "BREACH" | "INCOMPLETE";
  max_fact_age_seconds: string;
  fact_type: string;
  metric_key: string;
  comparator: "GT" | "GTE" | "LT" | "LTE" | "EQ" | "OCCURRED";
  numeric_threshold: string;
  event_after: string;
};

const FACT_TYPES = [
  "PRICE", "VOLUME", "TECHNICAL", "FUNDAMENTAL", "COMPANY_EVENT",
  "MACRO", "SENTIMENT", "THESIS_STATE", "PORTFOLIO_RISK",
];

const FACT_CONFIG: Record<string, { placeholder: string; help: string; requiresInstrument: boolean }> = {
  PRICE: { placeholder: "last", help: "当前价格固定使用 last。", requiresInstrument: true },
  VOLUME: { placeholder: "volume", help: "日线成交量固定使用 volume。", requiresInstrument: true },
  TECHNICAL: { placeholder: "rsi_14", help: "例如 rsi_14、macd、macd_signal、macd_histogram、atr_14。", requiresInstrument: true },
  FUNDAMENTAL: { placeholder: "revenue", help: "A 股使用标准财务指标代码；美股可用 revenue 或 reported:revenue。", requiresInstrument: true },
  COMPANY_EVENT: { placeholder: "ANY", help: "ANY 表示任意公司事件；比较方式必须为“已发生”。", requiresInstrument: true },
  MACRO: { placeholder: "CPIAUCSL", help: "填写 FRED series ID，例如 CPIAUCSL。", requiresInstrument: false },
  SENTIMENT: { placeholder: "sample_count", help: "美股可用 sample_count、weighted_score、disagreement；也可追加来源。", requiresInstrument: true },
  THESIS_STATE: { placeholder: "status:thesis_…:active", help: "例如 status:<thesis_id>:active 或 hard_invalidation_triggered:<thesis_id>。", requiresInstrument: false },
  PORTFOLIO_RISK: { placeholder: "overall_status", help: "组合风险固定使用 overall_status，数值等级为 PASS=0、WARN=1、BREACH=2、INCOMPLETE=3。", requiresInstrument: false },
};

function factTypePatch(factType: string): Partial<RuleDraft> {
  if (factType === "PRICE") return { fact_type: factType, metric_key: "last" };
  if (factType === "VOLUME") return { fact_type: factType, metric_key: "volume" };
  if (factType === "COMPANY_EVENT") return { fact_type: factType, metric_key: "ANY", comparator: "OCCURRED", numeric_threshold: "" };
  if (factType === "PORTFOLIO_RISK") return { fact_type: factType, metric_key: "overall_status", instrument_id: "" };
  if (factType === "MACRO" || factType === "THESIS_STATE") return { fact_type: factType, instrument_id: "" };
  return { fact_type: factType };
}

function blankRule(instrumentId = ""): RuleDraft {
  return {
    rule_code: "",
    description: "",
    rule_type: "PRICE_ABOVE",
    severity: "MEDIUM",
    instrument_id: instrumentId,
    price_threshold: "",
    risk_status_threshold: "WARN",
    max_fact_age_seconds: "7200",
    fact_type: "PRICE",
    metric_key: "last",
    comparator: "GT",
    numeric_threshold: "",
    event_after: "",
  };
}

function asLocalDateTime(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function fromMonitorRule(value: Dict, primary: string): RuleDraft {
  return {
    ...blankRule(primary),
    rule_code: String(value.rule_code ?? ""),
    description: String(value.description ?? ""),
    rule_type: String(value.rule_type ?? "PRICE_ABOVE") as RuleType,
    severity: String(value.severity ?? "MEDIUM") as RuleDraft["severity"],
    instrument_id: String(value.instrument_id ?? primary),
    price_threshold: String(value.price_threshold ?? ""),
    risk_status_threshold: String(value.risk_status_threshold ?? "WARN") as RuleDraft["risk_status_threshold"],
    max_fact_age_seconds: String(value.max_fact_age_seconds ?? 7200),
    fact_type: String(value.fact_type ?? "PRICE"),
    metric_key: String(value.metric_key ?? ""),
    comparator: String(value.comparator ?? "GT") as RuleDraft["comparator"],
    numeric_threshold: String(value.numeric_threshold ?? ""),
    event_after: asLocalDateTime(value.event_after),
  };
}

export function MonitorEditor({
  initialMonitor,
  onClose,
  onSaved,
  embedded = false,
}: {
  initialMonitor?: Dict;
  onClose: () => void;
  onSaved: (receipt: unknown) => void;
  embedded?: boolean;
}) {
  type ResolverMarket = "US" | "A_SHARE" | "KR";
  const editing = Boolean(initialMonitor);
  const primaryInitial = String(initialMonitor?.primary_instrument_id ?? "");
  const sourceRules = Array.isArray(initialMonitor?.rules) ? initialMonitor.rules as Dict[] : [];
  const [name, setName] = useState(String(initialMonitor?.name ?? ""));
  const [market, setMarket] = useState<ResolverMarket>(
    primaryInitial.includes(":A_SHARE:")
      ? "A_SHARE"
      : primaryInitial.includes(":KR:")
        ? "KR"
        : "US",
  );
  const [symbolQuery, setSymbolQuery] = useState(primaryInitial.split(":").at(-1) ?? "");
  const [primaryInstrumentId, setPrimaryInstrumentId] = useState(primaryInitial);
  const [caseId, setCaseId] = useState(String(initialMonitor?.case_id ?? ""));
  const [cadence, setCadence] = useState(String(initialMonitor?.cadence ?? "US_POST_MARKET"));
  const [intervalMinutes, setIntervalMinutes] = useState(String(initialMonitor?.interval_minutes ?? 60));
  const [status, setStatus] = useState(String(initialMonitor?.status ?? "ACTIVE"));
  const [validUntil, setValidUntil] = useState(asLocalDateTime(initialMonitor?.valid_until));
  const [rules, setRules] = useState<RuleDraft[]>(
    sourceRules.length ? sourceRules.map((rule) => fromMonitorRule(rule, primaryInitial)) : [blankRule(primaryInitial)],
  );
  const [resolving, setResolving] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<unknown>(null);
  const [idempotencyKey] = useState(
    () => `console-monitor-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  );

  function updateRule(index: number, patch: Partial<RuleDraft>) {
    setRules((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  function updateMarket(nextMarket: ResolverMarket) {
    setMarket(nextMarket);
    const postMarketCadences = new Set([
      "A_SHARE_POST_MARKET",
      "US_POST_MARKET",
      "KR_POST_MARKET",
    ]);
    if (postMarketCadences.has(cadence)) {
      setCadence(`${nextMarket}_POST_MARKET`);
    }
  }

  async function resolveInstrument() {
    if (!symbolQuery.trim()) return;
    setResolving(true);
    setError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "instrument_resolve",
        arguments: { market, query: symbolQuery.trim() },
      });
      const envelope = response.result as Dict | undefined;
      const data = envelope?.data as Dict | undefined;
      const instrument = data?.instrument as Dict | undefined;
      const instrumentId = typeof instrument?.instrument_id === "string" ? instrument.instrument_id : "";
      if (!instrumentId) throw new Error("没有找到唯一标的，请检查市场和代码。");
      setPrimaryInstrumentId(instrumentId);
      setRules((items) => items.map((item) => item.instrument_id ? item : { ...item, instrument_id: instrumentId }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "标的解析失败");
    } finally {
      setResolving(false);
    }
  }

  function normalizedRules(): Dict[] {
    return rules.map((rule) => {
      const base: Dict = {
        rule_code: rule.rule_code.trim(),
        description: rule.description.trim(),
        rule_type: rule.rule_type,
        severity: rule.severity,
        max_fact_age_seconds: Number(rule.max_fact_age_seconds),
      };
      if (rule.rule_type === "PRICE_ABOVE" || rule.rule_type === "PRICE_BELOW") {
        return { ...base, instrument_id: rule.instrument_id.trim(), price_threshold: rule.price_threshold.trim() };
      }
      if (rule.rule_type === "RISK_OVERALL_AT_LEAST") {
        return { ...base, risk_status_threshold: rule.risk_status_threshold };
      }
      return {
        ...base,
        ...(rule.instrument_id.trim() ? { instrument_id: rule.instrument_id.trim() } : {}),
        fact_type: rule.fact_type,
        metric_key: rule.metric_key.trim(),
        comparator: rule.comparator,
        ...(rule.comparator !== "OCCURRED" ? { numeric_threshold: rule.numeric_threshold.trim() } : {}),
        ...(rule.event_after ? { event_after: new Date(rule.event_after).toISOString() } : {}),
      };
    });
  }

  function validate(): string | null {
    if (!name.trim()) return "请填写 Monitor 名称。";
    if (rules.length === 0) return "至少需要一条条件。";
    for (const [index, rule] of rules.entries()) {
      if (!rule.rule_code.trim()) return `第 ${index + 1} 条条件缺少 rule code。`;
      if (!rule.description.trim()) return `第 ${index + 1} 条条件缺少具体释义。`;
      if (!Number.isInteger(Number(rule.max_fact_age_seconds)) || Number(rule.max_fact_age_seconds) <= 0) return `第 ${index + 1} 条条件的数据时效必须是正整数秒。`;
      if ((rule.rule_type === "PRICE_ABOVE" || rule.rule_type === "PRICE_BELOW") && (!rule.instrument_id.trim() || !(Number(rule.price_threshold) > 0))) return `第 ${index + 1} 条价格条件需要标的和正数阈值。`;
      if (rule.rule_type === "FACT_COMPARISON" && !rule.metric_key.trim()) return `第 ${index + 1} 条事实条件需要 metric key。`;
      if (rule.rule_type === "FACT_COMPARISON" && FACT_CONFIG[rule.fact_type]?.requiresInstrument && !rule.instrument_id.trim()) return `第 ${index + 1} 条 ${rule.fact_type} 条件需要标的。`;
      if (rule.rule_type === "FACT_COMPARISON" && rule.fact_type === "COMPANY_EVENT" && rule.comparator !== "OCCURRED") return `第 ${index + 1} 条公司事件条件必须使用“已发生”。`;
      if (rule.rule_type === "FACT_COMPARISON" && rule.fact_type === "PORTFOLIO_RISK" && rule.metric_key !== "overall_status") return `第 ${index + 1} 条组合风险条件必须使用 overall_status。`;
      if (rule.rule_type === "FACT_COMPARISON" && rule.comparator !== "OCCURRED" && (!rule.numeric_threshold.trim() || !Number.isFinite(Number(rule.numeric_threshold)))) return `第 ${index + 1} 条事实条件需要数值阈值。`;
    }
    if (cadence === "INTERVAL" && (Number(intervalMinutes) < 60 || Number(intervalMinutes) % 60 !== 0)) return "INTERVAL 必须是至少 60 分钟的整小时。";
    return null;
  }

  async function submit() {
    const validation = validate();
    if (validation) { setError(validation); return; }
    if (!window.confirm(editing ? "确认保存新的 Monitor 版本？" : "确认创建并启用这个 Monitor？")) return;
    const request: Dict = {
      operation: editing ? "update" : "create",
      name: name.trim(),
      cadence,
      confirmed_by: "user",
      idempotency_key: idempotencyKey,
      rules: normalizedRules(),
      ...(primaryInstrumentId ? { primary_instrument_id: primaryInstrumentId } : {}),
      ...(caseId.trim() ? { case_id: caseId.trim() } : {}),
      ...(cadence === "INTERVAL" ? { interval_minutes: Number(intervalMinutes) } : {}),
      ...(validUntil ? { valid_until: new Date(validUntil).toISOString() } : {}),
      ...(editing ? {
        monitor_id: initialMonitor?.monitor_id,
        expected_version: initialMonitor?.version,
        status,
      } : {}),
    };
    setSaving(true);
    setError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "monitor_manage",
        arguments: { request },
        confirmation: "monitor_manage",
      });
      const envelope = response.result as Dict | undefined;
      if (envelope?.ok === false) {
        throw new Error(displayJson(envelope.errors ?? "Monitor 保存失败"));
      }
      setReceipt(response);
      onSaved(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Monitor 保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={`monitor-editor${embedded ? " monitor-editor-embedded" : ""}`}>
      <header><div><p className="card-kicker">MONITOR BUILDER</p><h2>{editing ? "编辑 Monitor" : "新建 Monitor"}</h2></div><button className="close-button" type="button" onClick={onClose}>关闭</button></header>
      <div className="monitor-form-grid">
        <label><span>名称</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：TTWO 关键价位监控" /></label>
        <label><span>关联 Case ID（可选）</span><input value={caseId} onChange={(event) => setCaseId(event.target.value)} placeholder="case_…" /></label>
        <div className="instrument-resolver"><label><span>市场</span><select value={market} onChange={(event) => updateMarket(event.target.value as ResolverMarket)}><option value="US">美股</option><option value="A_SHARE">A 股</option><option value="KR">韩股</option></select></label><label><span>代码/名称</span><input value={symbolQuery} onChange={(event) => setSymbolQuery(event.target.value)} placeholder="TTWO / 600519 / 005930" /></label><ActionButton onClick={resolveInstrument} busy={resolving}>解析标的</ActionButton></div>
        <label><span>主标的 Instrument ID</span><input value={primaryInstrumentId} onChange={(event) => setPrimaryInstrumentId(event.target.value)} placeholder="equity:US:TTWO" /><small>解析后自动填入，也可以直接输入规范 ID。</small></label>
        <label><span>Cadence</span><select value={cadence} onChange={(event) => setCadence(event.target.value)}><option value="ON_DEMAND">按需</option><option value="INTERVAL">固定间隔</option><option value="A_SHARE_POST_MARKET">A 股收盘后</option><option value="US_POST_MARKET">美股收盘后</option><option value="KR_POST_MARKET">韩股收盘后</option></select></label>
        {cadence === "INTERVAL" && <label><span>间隔分钟</span><input type="number" min="60" step="60" value={intervalMinutes} onChange={(event) => setIntervalMinutes(event.target.value)} /></label>}
        {editing && <label><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="ACTIVE">ACTIVE</option><option value="PAUSED">PAUSED</option><option value="ARCHIVED">ARCHIVED</option></select></label>}
        <label><span>有效期（可选）</span><input type="datetime-local" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label>
      </div>

      <div className="rules-heading"><div><p className="card-kicker">CONDITIONS</p><h3>触发条件</h3></div><ActionButton onClick={() => setRules((items) => [...items, blankRule(primaryInstrumentId)])}>添加条件</ActionButton></div>
      <div className="rule-editor-list">
        {rules.map((rule, index) => (
          <article className="rule-editor" key={`rule-${index}`}>
            <header><strong>条件 {index + 1}</strong><button type="button" onClick={() => setRules((items) => items.filter((_, itemIndex) => itemIndex !== index))}>删除</button></header>
            <div className="rule-editor-grid">
              <label><span>Rule code</span><input value={rule.rule_code} onChange={(event) => updateRule(index, { rule_code: event.target.value.toUpperCase().replaceAll(" ", "_") })} placeholder="TTWO_FIRST_ZONE_FAIL" /><small>稳定机器标识；实际点位填写在阈值字段。</small></label>
              <label className="rule-description-input"><span>具体释义</span><input value={rule.description} onChange={(event) => updateRule(index, { description: event.target.value })} placeholder="例如：第一支撑区失效" maxLength={500} /></label>
              <label><span>类型</span><select value={rule.rule_type} onChange={(event) => updateRule(index, { rule_type: event.target.value as RuleType })}><option value="PRICE_ABOVE">价格高于</option><option value="PRICE_BELOW">价格低于</option><option value="FACT_COMPARISON">事实比较</option><option value="RISK_OVERALL_AT_LEAST">组合风险至少</option></select></label>
              <label><span>严重度</span><select value={rule.severity} onChange={(event) => updateRule(index, { severity: event.target.value as RuleDraft["severity"] })}><option value="INFO">INFO</option><option value="MEDIUM">MEDIUM</option><option value="HIGH">HIGH</option></select></label>
              <label><span>最大事实年龄（秒）</span><input type="number" min="1" value={rule.max_fact_age_seconds} onChange={(event) => updateRule(index, { max_fact_age_seconds: event.target.value })} /></label>
              {(rule.rule_type === "PRICE_ABOVE" || rule.rule_type === "PRICE_BELOW") && <><label><span>标的</span><input value={rule.instrument_id} onChange={(event) => updateRule(index, { instrument_id: event.target.value })} placeholder={primaryInstrumentId || "equity:US:TTWO"} /></label><label><span>价格阈值</span><input inputMode="decimal" value={rule.price_threshold} onChange={(event) => updateRule(index, { price_threshold: event.target.value })} placeholder="250.00" /></label></>}
              {rule.rule_type === "RISK_OVERALL_AT_LEAST" && <label><span>风险阈值</span><select value={rule.risk_status_threshold} onChange={(event) => updateRule(index, { risk_status_threshold: event.target.value as RuleDraft["risk_status_threshold"] })}><option value="WARN">WARN</option><option value="BREACH">BREACH</option></select></label>}
              {rule.rule_type === "FACT_COMPARISON" && <><label><span>标的{FACT_CONFIG[rule.fact_type]?.requiresInstrument ? "" : "（可选）"}</span><input value={rule.instrument_id} onChange={(event) => updateRule(index, { instrument_id: event.target.value })} placeholder={FACT_CONFIG[rule.fact_type]?.requiresInstrument ? (primaryInstrumentId || "equity:US:TTWO") : "该事实类型通常不需要标的"} /></label><label><span>事实类型</span><select value={rule.fact_type} onChange={(event) => updateRule(index, factTypePatch(event.target.value))}>{FACT_TYPES.map((value) => <option value={value} key={value}>{value}</option>)}</select></label><label><span>Metric key</span><input value={rule.metric_key} onChange={(event) => updateRule(index, { metric_key: event.target.value })} placeholder={FACT_CONFIG[rule.fact_type]?.placeholder} readOnly={["PRICE", "VOLUME", "PORTFOLIO_RISK"].includes(rule.fact_type)} /><small>{FACT_CONFIG[rule.fact_type]?.help}</small></label><label><span>比较</span><select value={rule.comparator} onChange={(event) => updateRule(index, { comparator: event.target.value as RuleDraft["comparator"] })} disabled={rule.fact_type === "COMPANY_EVENT"}><option value="GT">&gt;</option><option value="GTE">≥</option><option value="LT">&lt;</option><option value="LTE">≤</option><option value="EQ">=</option><option value="OCCURRED">已发生</option></select></label>{rule.comparator !== "OCCURRED" && <label><span>数值阈值</span><input inputMode="decimal" value={rule.numeric_threshold} onChange={(event) => updateRule(index, { numeric_threshold: event.target.value })} /></label>}<label><span>事件起点（可选）</span><input type="datetime-local" value={rule.event_after} onChange={(event) => updateRule(index, { event_after: event.target.value })} /></label></>}
            </div>
          </article>
        ))}
      </div>
      {error && <div className="inline-error">{error}</div>}
      {receipt !== null && <div className="save-success"><Badge value="SUCCEEDED" /> Monitor 已保存。</div>}
      <footer><span>保存会创建不可变版本；不会修改 Thesis、持仓或订单。</span><div><button className="close-button" type="button" onClick={onClose}>取消</button><ActionButton onClick={submit} busy={saving}>{editing ? "保存新版本" : "创建 Monitor"}</ActionButton></div></footer>
    </section>
  );
}
