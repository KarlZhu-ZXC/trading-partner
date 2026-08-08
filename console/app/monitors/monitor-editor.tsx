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
  recovery_threshold: string;
  technical_interval: "1d" | "1w";
  event_after: string;
};

const FACT_TYPES = [
  "PRICE", "VOLUME", "TECHNICAL", "FUNDAMENTAL", "COMPANY_EVENT",
  "MACRO", "SENTIMENT", "THESIS_STATE", "PORTFOLIO_RISK",
];

const FACT_CONFIG: Record<string, { placeholder: string; help: string; requiresInstrument: boolean }> = {
  PRICE: { placeholder: "last", help: "Current price always uses last.", requiresInstrument: true },
  VOLUME: { placeholder: "volume", help: "Daily volume always uses volume.", requiresInstrument: true },
  TECHNICAL: { placeholder: "rsi_14", help: "Examples: rsi_14, macd, macd_signal, macd_histogram, atr_14.", requiresInstrument: true },
  FUNDAMENTAL: { placeholder: "revenue", help: "A-shares use normalized metric codes; US equities support revenue or reported:revenue.", requiresInstrument: true },
  COMPANY_EVENT: { placeholder: "ANY", help: "ANY matches any company event; comparator must be OCCURRED.", requiresInstrument: true },
  MACRO: { placeholder: "CPIAUCSL", help: "Enter a FRED series ID such as CPIAUCSL.", requiresInstrument: false },
  SENTIMENT: { placeholder: "sample_count", help: "US equities support sample_count, weighted_score, and disagreement, optionally with a source suffix.", requiresInstrument: true },
  THESIS_STATE: { placeholder: "status:thesis_…:active", help: "Example: status:<thesis_id>:active or hard_invalidation_triggered:<thesis_id>.", requiresInstrument: false },
  PORTFOLIO_RISK: { placeholder: "overall_status", help: "Portfolio risk always uses overall_status: PASS=0, WARN=1, BREACH=2, INCOMPLETE=3.", requiresInstrument: false },
};

const TECHNICAL_METRICS = [
  "rsi_14", "macd", "macd_signal", "macd_histogram", "atr_14", "atr_percent",
  "ema_10", "ema_20", "sma_50", "sma_200", "bollinger_upper", "bollinger_mid",
  "bollinger_lower", "bollinger_width", "adx_14", "plus_di_14", "minus_di_14",
  "stochastic_k", "stochastic_d", "roc_20", "mfi_14", "vwma_20", "obv",
  "relative_volume_20",
];

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
    recovery_threshold: "",
    technical_interval: "1d",
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
    recovery_threshold: String(value.recovery_threshold ?? ""),
    technical_interval: String(value.technical_interval ?? "1d") as "1d" | "1w",
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
  const [subjectId, setSubjectId] = useState(String(initialMonitor?.subject_id ?? ""));
  const [cadence, setCadence] = useState(String(initialMonitor?.cadence ?? "US_POST_MARKET"));
  const [intervalMinutes, setIntervalMinutes] = useState(String(initialMonitor?.interval_minutes ?? 60));
  const [status, setStatus] = useState(String(initialMonitor?.status ?? "ACTIVE"));
  const [validUntil, setValidUntil] = useState(asLocalDateTime(initialMonitor?.valid_until));
  const initialJudgment = (initialMonitor?.judgment_policy ?? {}) as Dict;
  const [judgmentEnabled, setJudgmentEnabled] = useState(Boolean(initialMonitor?.judgment_policy));
  const [judgmentPlaybook, setJudgmentPlaybook] = useState(String(initialJudgment.playbook ?? ""));
  const [judgmentInstruments, setJudgmentInstruments] = useState(
    Array.isArray(initialJudgment.reference_instrument_ids)
      ? initialJudgment.reference_instrument_ids.join("\n")
      : "",
  );
  const [relativePairs, setRelativePairs] = useState(
    Array.isArray(initialJudgment.relative_strength_pairs)
      ? initialJudgment.relative_strength_pairs
        .map((item) => Array.isArray(item) ? item.join(" | ") : "")
        .join("\n")
      : "",
  );
  const [confirmedState, setConfirmedState] = useState(
    JSON.stringify(initialJudgment.confirmed_state ?? {}, null, 2),
  );
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
      if (!instrumentId) throw new Error("No unique instrument was found. Check the market and symbol.");
      setPrimaryInstrumentId(instrumentId);
      setRules((items) => items.map((item) => item.instrument_id ? item : { ...item, instrument_id: instrumentId }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Instrument resolution failed");
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
        ...(rule.recovery_threshold.trim() ? { recovery_threshold: rule.recovery_threshold.trim() } : {}),
        ...(rule.fact_type === "TECHNICAL" ? { technical_interval: rule.technical_interval } : {}),
        ...(rule.event_after ? { event_after: new Date(rule.event_after).toISOString() } : {}),
      };
    });
  }

  function validate(): string | null {
    if (!name.trim()) return "Enter a Monitor name.";
    if (rules.length === 0) return "At least one condition is required.";
    for (const [index, rule] of rules.entries()) {
      if (!rule.rule_code.trim()) return `Condition ${index + 1} is missing a rule code.`;
      if (!rule.description.trim()) return `Condition ${index + 1} is missing a human-readable meaning.`;
      if (!Number.isInteger(Number(rule.max_fact_age_seconds)) || Number(rule.max_fact_age_seconds) <= 0) return `Condition ${index + 1} fact age must be a positive integer number of seconds.`;
      if ((rule.rule_type === "PRICE_ABOVE" || rule.rule_type === "PRICE_BELOW") && (!rule.instrument_id.trim() || !(Number(rule.price_threshold) > 0))) return `Price condition ${index + 1} requires an instrument and a positive threshold.`;
      if (rule.rule_type === "FACT_COMPARISON" && !rule.metric_key.trim()) return `Fact condition ${index + 1} requires a metric key.`;
      if (rule.rule_type === "FACT_COMPARISON" && FACT_CONFIG[rule.fact_type]?.requiresInstrument && !rule.instrument_id.trim()) return `${rule.fact_type} condition ${index + 1} requires an instrument.`;
      if (rule.rule_type === "FACT_COMPARISON" && rule.fact_type === "COMPANY_EVENT" && rule.comparator !== "OCCURRED") return `Company event condition ${index + 1} must use OCCURRED.`;
      if (rule.rule_type === "FACT_COMPARISON" && rule.fact_type === "PORTFOLIO_RISK" && rule.metric_key !== "overall_status") return `Portfolio risk condition ${index + 1} must use overall_status.`;
      if (rule.rule_type === "FACT_COMPARISON" && rule.comparator !== "OCCURRED" && (!rule.numeric_threshold.trim() || !Number.isFinite(Number(rule.numeric_threshold)))) return `Fact condition ${index + 1} requires a numeric threshold.`;
      if (rule.recovery_threshold.trim() && !Number.isFinite(Number(rule.recovery_threshold))) return `Recovery threshold ${index + 1} must be numeric.`;
      if (rule.recovery_threshold.trim() && ["EQ", "OCCURRED"].includes(rule.comparator)) return `Comparator ${index + 1} does not support a recovery threshold.`;
      if (rule.recovery_threshold.trim() && ["GT", "GTE"].includes(rule.comparator) && Number(rule.recovery_threshold) >= Number(rule.numeric_threshold)) return `Recovery threshold ${index + 1} must be below the trigger threshold for an upward rule.`;
      if (rule.recovery_threshold.trim() && ["LT", "LTE"].includes(rule.comparator) && Number(rule.recovery_threshold) <= Number(rule.numeric_threshold)) return `Recovery threshold ${index + 1} must be above the trigger threshold for a downward rule.`;
    }
    if (cadence === "INTERVAL" && (Number(intervalMinutes) < 60 || Number(intervalMinutes) % 60 !== 0)) return "INTERVAL must be a whole-hour interval of at least 60 minutes.";
    if (judgmentEnabled) {
      if (!judgmentPlaybook.trim()) return "LLM judgment requires a Playbook.";
      if (!judgmentInstruments.split("\n").some((item) => item.trim())) return "LLM judgment requires at least one reference instrument.";
      try { JSON.parse(confirmedState); } catch { return "Confirmed state must be valid JSON."; }
      for (const line of relativePairs.split("\n").filter((item) => item.trim())) {
        if (line.split("|").map((item) => item.trim()).length !== 3) return "Each relative-strength line must be: name | numerator instrument | denominator instrument.";
      }
    }
    return null;
  }

  async function submit() {
    const validation = validate();
    if (validation) { setError(validation); return; }
    if (!window.confirm(editing ? "Save a new Monitor version?" : "Create and activate this Monitor?")) return;
    const request: Dict = {
      operation: editing ? "update" : "create",
      name: name.trim(),
      cadence,
      confirmed_by: "user",
      idempotency_key: idempotencyKey,
      rules: normalizedRules(),
      ...(primaryInstrumentId ? { primary_instrument_id: primaryInstrumentId } : {}),
      ...(subjectId.trim() ? { case_id: subjectId.trim() } : {}),
      ...(cadence === "INTERVAL" ? { interval_minutes: Number(intervalMinutes) } : {}),
      ...(validUntil ? { valid_until: new Date(validUntil).toISOString() } : {}),
      ...(judgmentEnabled ? {
        judgment_policy: {
          playbook: judgmentPlaybook.trim(),
          reference_instrument_ids: judgmentInstruments.split("\n").map((item) => item.trim()).filter(Boolean),
          relative_strength_pairs: relativePairs.split("\n").map((line) => line.split("|").map((item) => item.trim())).filter((item) => item.length === 3).map(([pairName, numerator, denominator]) => ({ name: pairName, numerator_instrument_id: numerator, denominator_instrument_id: denominator })),
          confirmed_state: JSON.parse(confirmedState),
        },
      } : {}),
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
        throw new Error(displayJson(envelope.errors ?? "Unable to save Monitor"));
      }
      setReceipt(response);
      onSaved(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to save Monitor");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={`monitor-editor${embedded ? " monitor-editor-embedded" : ""}`}>
      <header><div><p className="card-kicker">MONITOR BUILDER</p><h2>{editing ? "Edit Monitor" : "New Monitor"}</h2></div><button className="close-button" type="button" onClick={onClose}>Close</button></header>
      <div className="monitor-form-grid">
        <label><span>Name</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Example: TTWO key-level monitor" /></label>
        <label><span>Research Subject ID (optional)</span><input value={subjectId} onChange={(event) => setSubjectId(event.target.value)} placeholder="case_…" /></label>
        <div className="instrument-resolver"><label><span>Market</span><select value={market} onChange={(event) => updateMarket(event.target.value as ResolverMarket)}><option value="US">US</option><option value="A_SHARE">A-Share</option><option value="KR">Korea</option></select></label><label><span>Symbol / Name</span><input value={symbolQuery} onChange={(event) => setSymbolQuery(event.target.value)} placeholder="TTWO / 600519 / 005930" /></label><ActionButton onClick={resolveInstrument} busy={resolving}>Resolve</ActionButton></div>
        <label><span>Primary Instrument ID</span><input value={primaryInstrumentId} onChange={(event) => setPrimaryInstrumentId(event.target.value)} placeholder="equity:US:TTWO" /><small>Filled after resolution, or enter a canonical ID directly.</small></label>
        <label><span>Cadence</span><select value={cadence} onChange={(event) => setCadence(event.target.value)}><option value="ON_DEMAND">On Demand</option><option value="INTERVAL">Fixed Interval</option><option value="A_SHARE_POST_MARKET">A-Share Post-Market</option><option value="US_POST_MARKET">US Post-Market</option><option value="KR_POST_MARKET">Korea Post-Market</option></select></label>
        {cadence === "INTERVAL" && <label><span>Interval Minutes</span><input type="number" min="60" step="60" value={intervalMinutes} onChange={(event) => setIntervalMinutes(event.target.value)} /></label>}
        {editing && <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="ACTIVE">ACTIVE</option><option value="PAUSED">PAUSED</option><option value="ARCHIVED">ARCHIVED</option></select></label>}
        <label><span>Valid Until (optional)</span><input type="datetime-local" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label>
      </div>

      <div className="judgment-editor">
        <label className="judgment-toggle"><input type="checkbox" checked={judgmentEnabled} onChange={(event) => setJudgmentEnabled(event.target.checked)} /><span>Enable Composite LLM Judgment</span></label>
        <p>Deterministic rules remain hard gates. The LLM interprets validated features only and cannot change confirmed positions, phases, or orders.</p>
        {judgmentEnabled && <div className="judgment-editor-grid">
          <label className="wide"><span>Playbook</span><textarea rows={10} value={judgmentPlaybook} onChange={(event) => setJudgmentPlaybook(event.target.value)} placeholder="Describe cross-instrument relationships, phases, triggers, and notification requirements." /></label>
          <label><span>Reference Instruments (one Instrument ID per line)</span><textarea rows={7} value={judgmentInstruments} onChange={(event) => setJudgmentInstruments(event.target.value)} placeholder={"commodity_spot:OTC:XAUUSD\netf:US:GDX\netf:US:GLD"} /></label>
          <label><span>Relative Strength (name | numerator | denominator)</span><textarea rows={7} value={relativePairs} onChange={(event) => setRelativePairs(event.target.value)} placeholder="GDX_GLD | etf:US:GDX | etf:US:GLD" /></label>
          <label className="wide"><span>User-Confirmed State (JSON)</span><textarea rows={7} value={confirmedState} onChange={(event) => setConfirmedState(event.target.value)} placeholder={'{"confirmed_position":50,"phase_B_remaining":"150-200"}'} /></label>
        </div>}
      </div>

      <div className="rules-heading"><div><p className="card-kicker">CONDITIONS</p><h3>Trigger Conditions</h3></div><ActionButton onClick={() => setRules((items) => [...items, blankRule(primaryInstrumentId)])}>Add Condition</ActionButton></div>
      <div className="rule-editor-list">
        {rules.map((rule, index) => (
          <article className="rule-editor" key={`rule-${index}`}>
            <header><strong>Condition {index + 1}</strong><button type="button" onClick={() => setRules((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Delete</button></header>
            <div className="rule-editor-grid">
              <label><span>Rule Code</span><input value={rule.rule_code} onChange={(event) => updateRule(index, { rule_code: event.target.value.toUpperCase().replaceAll(" ", "_") })} placeholder="TTWO_FIRST_ZONE_FAIL" /><small>Stable machine identity; enter the actual level in the threshold field.</small></label>
              <label className="rule-description-input"><span>Human Meaning</span><input value={rule.description} onChange={(event) => updateRule(index, { description: event.target.value })} placeholder="Example: First support zone failed" maxLength={500} /></label>
              <label><span>Type</span><select value={rule.rule_type} onChange={(event) => updateRule(index, { rule_type: event.target.value as RuleType })}><option value="PRICE_ABOVE">Price Above</option><option value="PRICE_BELOW">Price Below</option><option value="FACT_COMPARISON">Fact Comparison</option><option value="RISK_OVERALL_AT_LEAST">Portfolio Risk At Least</option></select></label>
              <label><span>Severity</span><select value={rule.severity} onChange={(event) => updateRule(index, { severity: event.target.value as RuleDraft["severity"] })}><option value="INFO">INFO</option><option value="MEDIUM">MEDIUM</option><option value="HIGH">HIGH</option></select></label>
              <label><span>Maximum Fact Age (seconds)</span><input type="number" min="1" value={rule.max_fact_age_seconds} onChange={(event) => updateRule(index, { max_fact_age_seconds: event.target.value })} /></label>
              {(rule.rule_type === "PRICE_ABOVE" || rule.rule_type === "PRICE_BELOW") && <><label><span>Instrument</span><input value={rule.instrument_id} onChange={(event) => updateRule(index, { instrument_id: event.target.value })} placeholder={primaryInstrumentId || "equity:US:TTWO"} /></label><label><span>Price Threshold</span><input inputMode="decimal" value={rule.price_threshold} onChange={(event) => updateRule(index, { price_threshold: event.target.value })} placeholder="250.00" /></label></>}
              {rule.rule_type === "RISK_OVERALL_AT_LEAST" && <label><span>Risk Threshold</span><select value={rule.risk_status_threshold} onChange={(event) => updateRule(index, { risk_status_threshold: event.target.value as RuleDraft["risk_status_threshold"] })}><option value="WARN">WARN</option><option value="BREACH">BREACH</option></select></label>}
              {rule.rule_type === "FACT_COMPARISON" && <>
                <label><span>Instrument{FACT_CONFIG[rule.fact_type]?.requiresInstrument ? "" : " (optional)"}</span><input value={rule.instrument_id} onChange={(event) => updateRule(index, { instrument_id: event.target.value })} placeholder={FACT_CONFIG[rule.fact_type]?.requiresInstrument ? (primaryInstrumentId || "equity:US:TTWO") : "This fact type usually does not require an instrument"} /></label>
                <label><span>Fact Type</span><select value={rule.fact_type} onChange={(event) => updateRule(index, factTypePatch(event.target.value))}>{FACT_TYPES.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
                <label><span>{rule.fact_type === "TECHNICAL" ? "Technical Metric" : "Metric Key"}</span><input list={rule.fact_type === "TECHNICAL" ? "technical-metric-presets" : undefined} value={rule.metric_key} onChange={(event) => updateRule(index, { metric_key: event.target.value })} placeholder={FACT_CONFIG[rule.fact_type]?.placeholder} readOnly={["PRICE", "VOLUME", "PORTFOLIO_RISK"].includes(rule.fact_type)} /><small>{FACT_CONFIG[rule.fact_type]?.help}</small></label>
                {rule.fact_type === "TECHNICAL" && <label><span>Metric Interval</span><select value={rule.technical_interval} onChange={(event) => updateRule(index, { technical_interval: event.target.value as "1d" | "1w" })}><option value="1d">Daily</option><option value="1w">Weekly</option></select></label>}
                <label><span>Comparator</span><select value={rule.comparator} onChange={(event) => updateRule(index, { comparator: event.target.value as RuleDraft["comparator"] })} disabled={rule.fact_type === "COMPANY_EVENT"}><option value="GT">&gt;</option><option value="GTE">≥</option><option value="LT">&lt;</option><option value="LTE">≤</option><option value="EQ">=</option><option value="OCCURRED">Occurred</option></select></label>
                {rule.comparator !== "OCCURRED" && <><label><span>Trigger Threshold</span><input inputMode="decimal" value={rule.numeric_threshold} onChange={(event) => updateRule(index, { numeric_threshold: event.target.value })} /></label><label><span>Recovery Threshold (optional)</span><input inputMode="decimal" value={rule.recovery_threshold} onChange={(event) => updateRule(index, { recovery_threshold: event.target.value })} placeholder={rule.comparator === "LT" || rule.comparator === "LTE" ? "Example: RSI triggers at 30, recovers at 35" : "Example: RSI triggers at 70, recovers at 65"} /><small>Creates a hysteresis band to prevent repeated alerts near the threshold.</small></label></>}
                <label><span>Event Start (optional)</span><input type="datetime-local" value={rule.event_after} onChange={(event) => updateRule(index, { event_after: event.target.value })} /></label>
              </>}
            </div>
          </article>
        ))}
      </div>
      <datalist id="technical-metric-presets">{TECHNICAL_METRICS.map((metric) => <option value={metric} key={metric} />)}</datalist>
      {error && <div className="inline-error">{error}</div>}
      {receipt !== null && <div className="save-success"><Badge value="SUCCEEDED" /> Monitor saved.</div>}
      <footer><span>Saving creates an immutable version and never changes a Thesis, position, or order.</span><div><button className="close-button" type="button" onClick={onClose}>Cancel</button><ActionButton onClick={submit} busy={saving}>{editing ? "Save New Version" : "Create Monitor"}</ActionButton></div></footer>
    </section>
  );
}
