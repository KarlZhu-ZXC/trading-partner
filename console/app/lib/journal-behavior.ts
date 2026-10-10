/** Presentation of durable Journal metrics; never a source of investment facts. */
import { asObject as asDict, textTrimmed } from "./coerce";

type Dict = Record<string, unknown>;

function upper(value: unknown, fallback = "UNKNOWN"): string {
  return textTrimmed(value, fallback).toUpperCase();
}

export const BEHAVIOR_PRIMARY_METRICS = [
  { name: "closed_active_trade_cycles", label: "Closed Active Cycles", kind: "count" },
  { name: "win_rate", label: "Win Rate", kind: "rate" },
  { name: "payoff_ratio", label: "Payoff Ratio · Amount", kind: "payoff_amount" },
  { name: "return_payoff_ratio", label: "Payoff Ratio · Return %", kind: "payoff_return" },
  { name: "plan_coverage", label: "Plan Coverage", kind: "rate" },
  { name: "pre_fill_decision_coverage", label: "Pre-Fill Decision Coverage", kind: "rate" },
] as const;

export const BEHAVIOR_SECONDARY_METRICS = [
  "wins", "losses", "flat", "avg_win", "avg_loss", "avg_win_return", "avg_loss_return", "average_holding_duration",
  "median_holding_duration", "turnover", "pre_fill_invalidation_proxy",
  "invalidation_adherence", "same_day_reentry", "entry_attempt_count",
  "same_entry_logic_attempt_count", "third_attempt_without_new_plan",
  "add_confirmation_risk_control", "planned_holding_period_mismatch",
  "no_action_count", "no_action_review_completion",
];

const BEHAVIOR_RATE_METRICS = new Set([
  "win_rate", "plan_coverage", "pre_fill_decision_coverage",
  "pre_fill_invalidation_proxy", "invalidation_adherence", "same_day_reentry",
  "third_attempt_without_new_plan", "add_confirmation_risk_control",
  "planned_holding_period_mismatch", "no_action_review_completion",
]);
const BEHAVIOR_MONEY_METRICS = new Set(["avg_win", "avg_loss"]);
const BEHAVIOR_RETURN_METRICS = new Set(["avg_win_return", "avg_loss_return"]);
const BEHAVIOR_DURATION_METRICS = new Set(["average_holding_duration", "median_holding_duration"]);
const BEHAVIOR_AVERAGE_METRICS = new Set(["entry_attempt_count"]);

type BehaviorMetricPresentation = {
  result: string;
  formula: string;
  status: "AVAILABLE" | "UNAVAILABLE" | "NOT_SUPPORTED" | "INCONSISTENT";
};

function metricNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function metricInteger(value: unknown): string {
  const parsed = metricNumber(value);
  return parsed == null ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(parsed);
}

export function metricLabel(name: string): string {
  return name.replaceAll(":", " · ").split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

export function exclusionLabel(code: string): string {
  const known: Record<string, string> = {
    FEES_UNAVAILABLE: "Fees unavailable",
    CYCLE_INCOMPLETE: "Incomplete Cycle",
    CLASSIFICATION_EXCLUDED: "Classification excluded",
    MISSING_PLAN: "No eligible pre-period Plan",
    MISSING_DECISION: "No eligible pre-fill Decision",
  };
  return known[code] ?? metricLabel(code);
}

function metricCurrency(metric: Dict): string {
  const currencies = Array.isArray(metric.native_currencies) ? metric.native_currencies as string[] : [];
  return currencies.length === 1 ? currencies[0] : "USD";
}

function metricMoney(value: unknown, metric: Dict): string {
  const parsed = metricNumber(value);
  if (parsed == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: metricCurrency(metric),
    maximumFractionDigits: 2,
  }).format(parsed);
}

function metricPercent(value: unknown): string {
  const parsed = metricNumber(value);
  return parsed == null ? "—" : `${(parsed * 100).toFixed(1)}%`;
}

function metricSampleLabel(metric: Dict, singular: string): string {
  const count = metricNumber(metric.denominator ?? metric.avg_count ?? metric.sample_count);
  const plural = singular === "loss" ? "losses" : `${singular}s`;
  if (count == null) return `— ${plural}`;
  return `${metricInteger(count)} ${count === 1 ? singular : plural}`;
}

function metricDuration(value: unknown): string {
  const seconds = metricNumber(value);
  if (seconds == null) return "—";
  const days = seconds / 86_400;
  return days >= 1 ? `${days.toFixed(1)}d` : `${(seconds / 3_600).toFixed(1)}h`;
}

function metricBaseStatus(metric: Dict): BehaviorMetricPresentation["status"] {
  const availability = upper(metric.availability, "AVAILABLE");
  if (availability === "NOT_SUPPORTED") return "NOT_SUPPORTED";
  if (availability !== "AVAILABLE") return "UNAVAILABLE";
  return metric.value == null ? "UNAVAILABLE" : "AVAILABLE";
}

function combinedMetricStatus(...metrics: Dict[]): BehaviorMetricPresentation["status"] {
  const statuses = metrics.map(metricBaseStatus);
  if (statuses.includes("NOT_SUPPORTED")) return "NOT_SUPPORTED";
  if (statuses.includes("UNAVAILABLE")) return "UNAVAILABLE";
  return "AVAILABLE";
}

export function ratePresentation(metric: Dict): BehaviorMetricPresentation {
  const numerator = metricNumber(metric.numerator);
  const denominator = metricNumber(metric.denominator);
  const wireValue = metricNumber(metric.value);
  const formula = `${metricInteger(metric.numerator)} ÷ ${metricInteger(metric.denominator)}`;
  const status = metricBaseStatus(metric);
  if (status !== "AVAILABLE" || numerator == null || denominator == null || denominator <= 0 || wireValue == null) {
    return { result: "—", formula, status };
  }
  const calculated = numerator / denominator;
  if (Math.abs(calculated - wireValue) > 1e-9) {
    return { result: "—", formula: `${formula} · payload value does not match`, status: "INCONSISTENT" };
  }
  return { result: `${(calculated * 100).toFixed(1)}%`, formula: `${formula} = ${(calculated * 100).toFixed(1)}%`, status };
}

export function payoffPresentation(summary: Dict, basis: "amount" | "return"): BehaviorMetricPresentation {
  const isReturn = basis === "return";
  const payoff = asDict(summary[isReturn ? "return_payoff_ratio" : "payoff_ratio"]);
  const avgWin = asDict(summary[isReturn ? "avg_win_return" : "avg_win"]);
  const avgLoss = asDict(summary[isReturn ? "avg_loss_return" : "avg_loss"]);
  const win = metricNumber(avgWin.value);
  const lossValue = metricNumber(avgLoss.value);
  const loss = Math.abs(lossValue ?? 0);
  const wireValue = metricNumber(payoff.value);
  const winCount = metricSampleLabel(avgWin, "win");
  const lossCount = metricSampleLabel(avgLoss, "loss");
  const formula = isReturn
    ? `Return %: ${metricPercent(avgWin.value)} avg win return (${winCount}) ÷ ${metricPercent(loss || null)} abs avg loss return (${lossCount})`
    : `Amount: ${metricMoney(avgWin.value, avgWin)} avg win (${winCount}) ÷ ${metricMoney(loss || null, avgLoss)} abs avg loss (${lossCount})`;
  const status = combinedMetricStatus(payoff, avgWin, avgLoss);
  if (status !== "AVAILABLE" || win == null || loss <= 0 || wireValue == null) {
    return { result: "—", formula, status };
  }
  const calculated = win / loss;
  if (Math.abs(calculated - wireValue) > 1e-9) {
    return { result: "—", formula: `${formula} · payload value does not match`, status: "INCONSISTENT" };
  }
  return { result: `${calculated.toFixed(2)}x`, formula: `${formula} = ${calculated.toFixed(2)}x`, status };
}

export function countPresentation(metric: Dict): BehaviorMetricPresentation {
  const status = metricBaseStatus(metric);
  return {
    result: metricInteger(metric.numerator),
    formula: `${metricInteger(metric.numerator)} counted ÷ ${metricInteger(metric.denominator)} selected`,
    status,
  };
}

export function secondaryMetricPresentation(name: string, metric: Dict): BehaviorMetricPresentation {
  const status = metricBaseStatus(metric);
  if (status === "NOT_SUPPORTED" || status === "UNAVAILABLE") return {
    result: "—",
    formula: "Not computable from current durable facts",
    status,
  };
  if (BEHAVIOR_RATE_METRICS.has(name)) return ratePresentation(metric);
  if (BEHAVIOR_RETURN_METRICS.has(name)) {
    const side = name === "avg_win_return" ? "winning" : "losing";
    const average = metricPercent(metric.value);
    return {
      result: status === "AVAILABLE" ? average : "—",
      formula: `${metricPercent(metric.numerator)} total ${side} returns ÷ ${metricInteger(metric.denominator)} ${side} Cycles${average === "—" ? "" : ` = ${average}`}`,
      status,
    };
  }
  if (BEHAVIOR_MONEY_METRICS.has(name)) return {
    result: status === "AVAILABLE" ? metricMoney(metric.value, metric) : "—",
    formula: `${metricMoney(metric.numerator, metric)} total ÷ ${metricInteger(metric.denominator)} Cycles`,
    status,
  };
  if (BEHAVIOR_DURATION_METRICS.has(name)) return {
    result: status === "AVAILABLE" ? metricDuration(metric.value) : "—",
    formula: name === "median_holding_duration"
      ? `Median of ${metricInteger(metric.denominator)} Cycles`
      : `${metricDuration(metric.numerator)} total ÷ ${metricInteger(metric.denominator)} Cycles`,
    status,
  };
  if (BEHAVIOR_AVERAGE_METRICS.has(name)) {
    const average = metricNumber(metric.value);
    return {
      result: status === "AVAILABLE" && average != null ? average.toFixed(2) : "—",
      formula: `${metricInteger(metric.numerator)} attempts ÷ ${metricInteger(metric.denominator)} Cycles${average == null ? "" : ` = ${average.toFixed(2)}`}`,
      status,
    };
  }
  return {
    result: status === "AVAILABLE" ? metricInteger(metric.value) : "—",
    formula: metric.denominator == null
      ? `${metricInteger(metric.numerator)} observations`
      : `${metricInteger(metric.numerator)} ÷ ${metricInteger(metric.denominator)}`,
    status,
  };
}
