"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ClipboardPenLine, RefreshCw } from "lucide-react";
import { ErrorNote, ActionButton, Badge, Card, ConfirmationDialog, DataBoundary, Disclosure, Empty, FormField, HorizontalTabs, PageActionMenu, Paginator, QuickLink, SortableTableHeader, TextInputDialog, formatDate, formatDecimal, shortId } from "../components/ui";
import { ConsoleShell } from "../components/console-shell";
import { MultiSelectAutosuggest, type AutosuggestOption } from "../components/multi-select-autosuggest";
import { envelopeData, getJson, listOf, postApi, useApi } from "../lib/api";
import { endOfDayIsoOrNull } from "../lib/review-due-date.mjs";
import { useAgentPageContext } from "../lib/agent-page-context";
import { ObservationInbox } from "./observation-inbox";
import { RetroReviewList } from "./retro-review-list";
import { ScenarioDigest } from "./scenario-digest";
import { CycleAdjustmentEditor } from "./cycle-adjustment-editor";

type Dict = Record<string, unknown>;

type JournalWorkbenchResponse = {
  selected_subject_id?: unknown;
  subjects?: SubjectAggregate[];
  partial_failures?: string[];
  review_items?: Dict[];
  review_item_metrics?: Dict;
  activity_annotations?: Dict[];
  order_intents?: Dict[];
  behavior_review_runs?: Dict[];
  accounts?: unknown;
  behavior?: unknown;
  performance_series?: unknown;
  timeline?: unknown;
  trade_cycles?: unknown;
  transactions?: unknown;
};

type ObservationInboxResponse = {
  data?: {
    external_notes?: Dict[];
    observation_sources?: Dict[];
    review_workflow_enabled?: boolean;
  };
};
type SubjectAggregate = { subject?: Dict; state?: Dict };
type NextStep = { key: string; severity: string; title: string; detail: string; href: string; reviewItem?: Dict };
type ReviewInput = { kind: "resolution" | "due"; item: Dict; status: "ACKNOWLEDGED" | "RESOLVED" };
type WeeklyReviewPreview = { start: string; end: string; nextStart: string; nextEnd: string };
type DecisionAction = "watch" | "no_action" | "initiate_intent" | "add_intent" | "hold" | "reduce_intent" | "exit_intent" | "avoid" | "research_more";
type DecisionScenario = "UPSIDE" | "SIDEWAYS" | "PULLBACK" | "INVALIDATION";
type JournalTab = "overview" | "cycles" | "behavior" | "notes" | "reviews" | "timeline";
type PeriodFilter = "ALL" | "30D" | "90D" | "YTD" | "CUSTOM";
type ActivityClassification = "ACTIVE_TRADE" | "LONG_TERM_INVESTMENT" | "HEDGE" | "CASH_MANAGEMENT" | "TRANSFER_OR_ADMIN" | "UNCLASSIFIED";
type CycleStatusFilter = "OPEN" | "CLOSED" | "UNRESOLVED";
type CycleSortMode = "LATEST_DESC" | "LATEST_ASC" | "OPENED_DESC" | "OPENED_ASC" | "INSTRUMENT_ASC" | "INSTRUMENT_DESC";
type InstrumentTableSortKey = "instrument" | "fills" | "bought" | "sold" | "accounts" | "closedCycles" | "knownPnl" | "lastTradeAt";
type InstrumentTableSort = { key: InstrumentTableSortKey; direction: "asc" | "desc" };
type InstrumentTradeRow = {
  instrument: string;
  fills: number;
  bought: number;
  sold: number;
  accounts: number;
  closedCycles: number;
  knownPnl: number;
  pnlCycles: number;
  firstTradeAt: string;
  lastTradeAt: string;
};

const INSTRUMENT_TABLE_PAGE_SIZE = 15;
const TIMELINE_PAGE_SIZE = 50;

const JOURNAL_TABS: Array<{ id: JournalTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "cycles", label: "Trade Cycles" },
  { id: "behavior", label: "Behavior" },
  { id: "notes", label: "View Inbox" },
  { id: "reviews", label: "Reviews" },
  { id: "timeline", label: "Timeline" },
];

const DECISION_ACTIONS: Array<{ value: DecisionAction; label: string }> = [
  { value: "no_action", label: "No Action" },
  { value: "watch", label: "Watch" },
  { value: "hold", label: "Hold" },
  { value: "initiate_intent", label: "Initiate Intent" },
  { value: "add_intent", label: "Add Intent" },
  { value: "reduce_intent", label: "Reduce Intent" },
  { value: "exit_intent", label: "Exit Intent" },
  { value: "avoid", label: "Avoid" },
  { value: "research_more", label: "Research More" },
];

const DECISION_SCENARIOS: DecisionScenario[] = ["UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION"];
const CLASSIFICATION_OPTIONS: AutosuggestOption[] = [
  { value: "ACTIVE_TRADE", label: "Active Trade" },
  { value: "LONG_TERM_INVESTMENT", label: "Long-Term Investment" },
  { value: "HEDGE", label: "Hedge" },
  { value: "CASH_MANAGEMENT", label: "Cash Management" },
  { value: "TRANSFER_OR_ADMIN", label: "Transfer or Admin" },
  { value: "UNCLASSIFIED", label: "Unclassified" },
];
const CYCLE_STATUS_OPTIONS: AutosuggestOption[] = [
  { value: "OPEN", label: "Open", description: "Position quantity remains above zero." },
  { value: "CLOSED", label: "Closed", description: "Matched activity returned quantity to zero." },
  { value: "UNRESOLVED", label: "Unresolved", description: "Available history cannot reconstruct a valid long-only Cycle." },
];

function asDict(value: unknown): Dict {
  return value && typeof value === "object" ? value as Dict : {};
}

function text(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function upper(value: unknown, fallback = "UNKNOWN"): string {
  return text(value, fallback).toUpperCase();
}

function number(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function periodStart(filter: PeriodFilter): number | null {
  if (filter === "ALL" || filter === "CUSTOM") return null;
  const now = new Date();
  if (filter === "YTD") return new Date(now.getFullYear(), 0, 1).getTime();
  const days = filter === "30D" ? 30 : 90;
  now.setHours(0, 0, 0, 0);
  now.setDate(now.getDate() - days);
  return now.getTime();
}

function selectedPeriodWindow(
  filter: PeriodFilter,
  customStart: string,
  customEnd: string,
): { start: string | null; end: string | null; valid: boolean } {
  const presetStart = periodStart(filter);
  const start = filter === "CUSTOM"
    ? startOfDayIsoOrNull(customStart)
    : presetStart === null
      ? null
      : new Date(presetStart).toISOString();
  const end = filter === "CUSTOM" ? endOfDayIsoOrNull(customEnd) : null;
  return {
    start,
    end,
    valid: !start || !end || Date.parse(start) <= Date.parse(end),
  };
}

function openReviewCountFor(
  subjectFilters: string[],
  reviewItems: Dict[],
  metrics: Dict,
): number {
  return subjectFilters.length > 1
    ? reviewItems.length
    : number(metrics.open_count) || reviewItems.length;
}

function currentTradePlan(state: Dict): Dict | null {
  return state.current_trade_plan && typeof state.current_trade_plan === "object"
    ? state.current_trade_plan as Dict
    : null;
}

function selectedInstrumentScope(
  explicit: string[],
  subjectInstruments: string[],
): string[] {
  if (explicit.length > 0) return explicit;
  return subjectInstruments.length > 0 ? Array.from(new Set(subjectInstruments)) : [];
}

function contextInstrumentScope(active: string[], fallback: string): string[] {
  if (active.length > 0) return active;
  return fallback ? [fallback] : [];
}

function dateInputValue(value = new Date()): string {
  return new Date(value.getTime() - value.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

function startOfDayIsoOrNull(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function cycleReviewTime(cycle: Dict): number {
  return Date.parse(text(cycle.closed_at, text(cycle.opened_at, ""))) || 0;
}

function cyclePageSizeForViewport(width: number, height: number): 4 | 6 | 8 | 10 {
  if (width <= 700 || height < 760) return 4;
  if (height < 930) return 6;
  if (height < 1_100) return 8;
  return 10;
}

function formatMoney(value: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

function journalReviewTitle(value: unknown): string {
  return text(value, "Review required").replace(/^Trade Retro\b/i, "Period Review");
}

function cyclePnlPresentation(cycle: Dict): {
  label: string;
  value: string;
  detail: string | null;
  tone: "positive" | "negative" | "";
} {
  const currency = text(cycle.currency, "USD");
  if (cycle.net_realized_pnl != null) {
    const value = number(cycle.net_realized_pnl);
    return {
      label: "Net P/L",
      value: formatMoney(value, currency),
      detail: "After known fees",
      tone: value < 0 ? "negative" : value > 0 ? "positive" : "",
    };
  }
  if (cycle.gross_realized_pnl != null) {
    const value = number(cycle.gross_realized_pnl);
    return {
      label: "Gross P/L",
      value: formatMoney(value, currency),
      detail: "Fees unavailable",
      tone: value < 0 ? "negative" : value > 0 ? "positive" : "",
    };
  }
  return { label: "P/L", value: "Unavailable", detail: null, tone: "" };
}

function cycleStatusTone(value: unknown): "good" | "bad" | "neutral" {
  const status = upper(value);
  if (status === "OPEN") return "good";
  if (status === "UNRESOLVED") return "bad";
  return "neutral";
}

function cycleQualityTone(value: unknown): "good" | "warn" | "neutral" {
  const quality = upper(value);
  if (quality === "COMPLETE") return "good";
  if (quality === "INCOMPLETE") return "warn";
  return "neutral";
}

function cycleClassificationTone(value: unknown): "good" | "warn" | "neutral" {
  const classification = upper(value);
  if (["ACTIVE_TRADE", "LONG_TERM_INVESTMENT"].includes(classification)) return "good";
  if (classification === "UNCLASSIFIED") return "warn";
  return "neutral";
}

function futureDateInput(days: number): string {
  const value = new Date();
  value.setDate(value.getDate() + days);
  return new Date(value.getTime() - value.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 10);
}

function unwrap(value: unknown): Dict {
  return envelopeData<Dict>(value) ?? asDict(value);
}

function stateOf(item: SubjectAggregate | undefined): Dict {
  return unwrap(item?.state);
}

function scorecardRuns(payload: Dict | null): Dict[] {
  return listOf<Dict>(unwrap(asDict(payload?.scorecards)), "runs");
}

function agendaItems(payload: Dict | null): Dict[] {
  return listOf<Dict>(unwrap(asDict(payload?.agenda)), "items");
}

function monitorItems(payload: Dict | null): Dict[] {
  return listOf<Dict>(unwrap(asDict(payload?.monitors)), "items");
}

function retroRuns(payload: Dict | null): Dict[] {
  return listOf<Dict>(unwrap(asDict(payload?.retro)), "runs");
}

function latestBy(items: Dict[], field: string): Dict | null {
  return [...items].sort((left, right) => {
    const leftTime = Date.parse(text(left[field], "")) || 0;
    const rightTime = Date.parse(text(right[field], "")) || 0;
    return rightTime - leftTime;
  })[0] ?? null;
}

function StepList({ items, busy, onTransition }: { items: NextStep[]; busy: string | null; onTransition: (item: Dict, status: "ACKNOWLEDGED" | "RESOLVED") => void }) {
  if (items.length === 0) {
    return <div className="decision-ready"><span aria-hidden="true">✓</span><div><strong>No Immediate Closure Gap</strong><small>Continue observing current facts and scheduled reviews.</small></div></div>;
  }
  return <div className="decision-next-list">{items.map((item) => <article key={item.key}><Link href={item.href}><Badge value={item.severity} /><div><strong>{item.title}</strong><span>{item.detail}</span></div><span aria-hidden="true">→</span></Link>{item.reviewItem ? <div className="review-item-actions"><ActionButton busy={busy === item.reviewItem.review_item_id} onClick={() => onTransition(item.reviewItem!, "ACKNOWLEDGED")}>{upper(item.reviewItem.status) === "ACKNOWLEDGED" ? "Update Due" : "Acknowledge"}</ActionButton><ActionButton busy={busy === item.reviewItem.review_item_id} tone="warning" onClick={() => onTransition(item.reviewItem!, "RESOLVED")}>Resolve</ActionButton></div> : null}</article>)}</div>;
}

const BEHAVIOR_PRIMARY_METRICS = [
  { name: "closed_active_trade_cycles", label: "Closed Active Cycles", kind: "count" },
  { name: "win_rate", label: "Win Rate", kind: "rate" },
  { name: "payoff_ratio", label: "Payoff Ratio", kind: "payoff" },
  { name: "plan_coverage", label: "Plan Coverage", kind: "rate" },
  { name: "pre_fill_decision_coverage", label: "Pre-Fill Decision Coverage", kind: "rate" },
] as const;

const BEHAVIOR_SECONDARY_METRICS = [
  "wins", "losses", "flat", "avg_win", "avg_loss", "average_holding_duration",
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
const BEHAVIOR_DURATION_METRICS = new Set(["average_holding_duration", "median_holding_duration"]);
const BEHAVIOR_AVERAGE_METRICS = new Set(["entry_attempt_count"]);

type BehaviorMetricPresentation = {
  result: string;
  formula: string;
  status: "AVAILABLE" | "LIMITED" | "UNAVAILABLE" | "NOT_SUPPORTED" | "INCONSISTENT";
};

function metricNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function metricInteger(value: unknown): string {
  const parsed = metricNumber(value);
  return parsed == null ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(parsed);
}

function metricLabel(name: string): string {
  return name.replaceAll(":", " · ").split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

function exclusionLabel(code: string): string {
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
  const currencies = listOf<string>(metric, "native_currencies");
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
  if (metric.sample_sufficient === false) return "LIMITED";
  return metric.value == null ? "UNAVAILABLE" : "AVAILABLE";
}

function ratePresentation(metric: Dict): BehaviorMetricPresentation {
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

function payoffPresentation(summary: Dict): BehaviorMetricPresentation {
  const payoff = asDict(summary.payoff_ratio);
  const avgWin = asDict(summary.avg_win);
  const avgLoss = asDict(summary.avg_loss);
  const win = metricNumber(avgWin.value);
  const loss = Math.abs(metricNumber(avgLoss.value) ?? 0);
  const wireValue = metricNumber(payoff.value);
  const formula = `${metricMoney(avgWin.value, avgWin)} avg win ÷ ${metricMoney(loss || null, avgLoss)} avg loss`;
  const status = metricBaseStatus(payoff);
  if (status !== "AVAILABLE" || win == null || loss <= 0 || wireValue == null) {
    return { result: "—", formula, status };
  }
  const calculated = win / loss;
  if (Math.abs(calculated - wireValue) > 1e-9) {
    return { result: "—", formula: `${formula} · payload value does not match`, status: "INCONSISTENT" };
  }
  return { result: calculated.toFixed(2), formula: `${formula} = ${calculated.toFixed(2)}`, status };
}

function countPresentation(metric: Dict): BehaviorMetricPresentation {
  const status = metricBaseStatus(metric);
  return {
    result: metricInteger(metric.numerator),
    formula: `${metricInteger(metric.numerator)} counted ÷ ${metricInteger(metric.denominator)} selected`,
    status,
  };
}

function secondaryMetricPresentation(name: string, metric: Dict): BehaviorMetricPresentation {
  const status = metricBaseStatus(metric);
  if (status === "NOT_SUPPORTED" || status === "UNAVAILABLE") return {
    result: "—",
    formula: "Not computable from current durable facts",
    status,
  };
  if (BEHAVIOR_RATE_METRICS.has(name)) return ratePresentation(metric);
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

function BehaviorPanel({ value }: { value: Dict }) {
  if (Object.keys(value).length === 0) return <Empty>Behavior summary is unavailable.</Empty>;
  const primary = BEHAVIOR_PRIMARY_METRICS.map((item) => {
    const metric = asDict(value[item.name]);
    const presentation = item.kind === "rate"
      ? ratePresentation(metric)
      : item.kind === "payoff"
        ? payoffPresentation(value)
        : countPresentation(metric);
    return { ...item, metric, presentation };
  });
  const scenarioMetrics = listOf<Dict>(value, "scenario_action_distribution");
  const secondary = [
    ...BEHAVIOR_SECONDARY_METRICS.map((name) => ({ name, metric: asDict(value[name]) })),
    ...scenarioMetrics.map((metric) => ({ name: text(metric.name, "scenario_action"), metric })),
  ];
  return <>
    <div className="behavior-primary-grid">{primary.map(({ name, label, metric, presentation }) => <article key={name}>
      <header><span>{label}</span><Badge value={presentation.status} tone={presentation.status === "AVAILABLE" ? "good" : presentation.status === "INCONSISTENT" ? "bad" : "warn"} /></header>
      <strong>{presentation.result}</strong>
      <code>{presentation.formula}</code>
      <small>{metricInteger(metric.excluded_count)} excluded · minimum sample {metricInteger(metric.minimum_sample_size)}</small>
    </article>)}</div>
    <Disclosure variant="compact" title="Other Metrics & Audit Details" meta={`${secondary.length} METRICS`}><div className="table-wrap behavior-audit-table"><table><thead><tr><th>Metric</th><th>Result</th><th>Calculation</th><th>Excluded</th><th>Status</th></tr></thead><tbody>{secondary.map(({ name, metric }) => { const presentation = secondaryMetricPresentation(name, metric); return <tr key={name}><td data-label="Metric"><strong>{metricLabel(name)}</strong>{metric.note ? <small className="table-sub">{text(metric.note)}</small> : null}</td><td data-label="Result">{presentation.result}</td><td data-label="Calculation"><code>{presentation.formula}</code></td><td data-label="Excluded">{metricInteger(metric.excluded_count)}<small className="table-sub">{listOf<string>(metric, "exclusion_reasons").map(exclusionLabel).join(" · ") || "None"}</small></td><td data-label="Status"><Badge value={presentation.status} tone={presentation.status === "AVAILABLE" ? "good" : presentation.status === "INCONSISTENT" ? "bad" : "warn"} />{metric.unavailable_reason ? <small className="table-sub">{exclusionLabel(text(metric.unavailable_reason))}</small> : null}</td></tr>; })}</tbody></table></div></Disclosure>
  </>;
}

function CurrentViewCard({
  subjectId,
  currentView,
  subject,
  loading,
  error,
}: {
  subjectId: string;
  currentView: Dict;
  subject: Dict;
  loading: boolean;
  error: string | null;
}) {
  let content: ReactNode;
  if (!subjectId) {
    content = <Empty>Select one Research Subject to inspect its confirmed view.</Empty>;
  } else if (loading) {
    content = <Empty>Loading the confirmed view…</Empty>;
  } else if (error) {
    content = <div className="inline-error">Current View read failed: {error}</div>;
  } else if (!currentView.review) {
    content = <Empty>No Moomoo-derived view has been reviewed and confirmed for this Research Subject yet. Open View Inbox to review the latest eligible change.</Empty>;
  } else {
    const decision = asDict(currentView.decision);
    const thesis = asDict(currentView.thesis);
    const plan = asDict(currentView.trade_plan);
    content = <div className="journal-current-view"><div><span>Decision</span><strong>{text(decision.title, "Confirmed Decision")}</strong><p>{text(decision.rationale, "No rationale recorded.")}</p></div><div><span>Thesis</span><strong>{text(thesis.title, "No live Thesis")}</strong><p>{text(thesis.statement, "The confirmed Decision remains available without a live Thesis.")}</p></div><div><span>Trade Plan</span><strong>{currentView.trade_plan ? `${shortId(plan.instrument_id)} · v${number(plan.version)}` : "No active Plan"}</strong><p>{currentView.trade_plan ? `${upper(plan.status)} · confirmed execution context` : "No execution plan is implied by this view."}</p></div><div><span>Source</span><strong>{text(currentView.source_title, "Imported Note")} · v{number(currentView.source_note_version)}</strong><p>Exact immutable Observation revision retained in provenance</p></div></div>;
  }
  return <Card kicker="JUDGMENT SYSTEM OF RECORD" title="Current Confirmed View" description={text(currentView.subject_title, text(subject.title, "Select a Research Subject"))} action={currentView.review ? <Badge value={upper(asDict(currentView.review).status, "CONFIRMED")} tone="good" /> : undefined}>{content}</Card>;
}

export default function DecisionWorkbenchPage() {
  const [requestedSubjectId, setRequestedSubjectId] = useState("");
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewInput, setReviewInput] = useState<ReviewInput | null>(null);
  const [reviewInputValue, setReviewInputValue] = useState("");
  const [reviewInputError, setReviewInputError] = useState<string | null>(null);
  const [reviewAcknowledgement, setReviewAcknowledgement] = useState<Dict | null>(null);
  const [decisionOpen, setDecisionOpen] = useState(false);
  const [decisionAction, setDecisionAction] = useState<DecisionAction>("no_action");
  const [decisionScenario, setDecisionScenario] = useState<DecisionScenario>("SIDEWAYS");
  const [decisionReason, setDecisionReason] = useState("");
  const [decisionSourceNote, setDecisionSourceNote] = useState<string | null>(null);
  const [decisionSourceRevisionId, setDecisionSourceRevisionId] = useState<string | null>(null);
  const [decisionSourceReview, setDecisionSourceReview] = useState<{ reviewId: string; version: number } | null>(null);
  const [decisionReviewPackage, setDecisionReviewPackage] = useState<Dict | null>(null);
  const [observationDefer, setObservationDefer] = useState<{ reviewId: string; version: number; title: string; dueDate: string } | null>(null);
  const [observationDeferBusy, setObservationDeferBusy] = useState(false);
  const [decisionDraftScenarios, setDecisionDraftScenarios] = useState<Dict[]>([]);
  const [decisionReviewDate, setDecisionReviewDate] = useState(() => futureDateInput(7));
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [decisionMessage, setDecisionMessage] = useState<string | null>(null);
  const [captureRequested, setCaptureRequested] = useState(false);
  const [captureContextError, setCaptureContextError] = useState<string | null>(null);
  const [supersedesDecisionId, setSupersedesDecisionId] = useState<string | null>(null);
  const [journalTab, setJournalTab] = useState<JournalTab>("overview");
  const [cycleOffset, setCycleOffset] = useState(0);
  const [cyclePageSize, setCyclePageSize] = useState<4 | 6 | 8 | 10>(6);
  const [behaviorReviewBusy, setBehaviorReviewBusy] = useState(false);
  const [behaviorReviewError, setBehaviorReviewError] = useState<string | null>(null);
  const [weeklyReviewPreview, setWeeklyReviewPreview] = useState<WeeklyReviewPreview | null>(null);
  const [noteSyncBusy, setNoteSyncBusy] = useState(false);
  const [noteSyncError, setNoteSyncError] = useState<string | null>(null);
  const [noteSyncMessage, setNoteSyncMessage] = useState<string | null>(null);
  const [noteAnalysisBusyId, setNoteAnalysisBusyId] = useState<string | null>(null);
  const [noteReviewBusyId, setNoteReviewBusyId] = useState<string | null>(null);
  const [periodFilter, setPeriodFilter] = useState<PeriodFilter>("ALL");
  const [customPeriodStart, setCustomPeriodStart] = useState(() => `${new Date().getFullYear()}-01-01`);
  const [customPeriodEnd, setCustomPeriodEnd] = useState(() => dateInputValue());
  const [accountFilters, setAccountFilters] = useState<string[]>([]);
  const [instrumentFilters, setInstrumentFilters] = useState<string[]>([]);
  const [subjectFilters, setSubjectFilters] = useState<string[]>([]);
  const [classificationFilters, setClassificationFilters] = useState<ActivityClassification[]>([]);
  const [qualityFilter, setQualityFilter] = useState("ALL");
  const [cycleStatusFilters, setCycleStatusFilters] = useState<CycleStatusFilter[]>([]);
  const [cycleSortMode, setCycleSortMode] = useState<CycleSortMode>("LATEST_DESC");
  const [selectedCycleId, setSelectedCycleId] = useState("");
  const [pendingNoteDecision, setPendingNoteDecision] = useState<Dict | null>(null);
  const [reviewOffset, setReviewOffset] = useState(0);
  const [timelineOffset, setTimelineOffset] = useState(0);
  const [instrumentTableFilters, setInstrumentTableFilters] = useState<string[]>([]);
  const [instrumentTableSort, setInstrumentTableSort] = useState<InstrumentTableSort>({ key: "lastTradeAt", direction: "desc" });
  const [instrumentTableOffset, setInstrumentTableOffset] = useState(0);
  const selectedPeriod = selectedPeriodWindow(
    periodFilter,
    customPeriodStart,
    customPeriodEnd,
  );
  const selectedPeriodStart = selectedPeriod.start;
  const selectedPeriodEnd = selectedPeriod.end;
  const periodWindowValid = selectedPeriod.valid;
  const workbenchQuery = [
    requestedSubjectId ? `subject_id=${encodeURIComponent(requestedSubjectId)}` : "",
    ...classificationFilters.map((value) => `classifications=${encodeURIComponent(value)}`),
    ...accountFilters.map((value) => `account_refs=${encodeURIComponent(value)}`),
    ...instrumentFilters.map((value) => `instrument_ids=${encodeURIComponent(value)}`),
    selectedPeriodStart && periodWindowValid ? `behavior_start=${encodeURIComponent(selectedPeriodStart)}` : "",
    selectedPeriodEnd && periodWindowValid ? `behavior_end=${encodeURIComponent(selectedPeriodEnd)}` : "",
  ].filter(Boolean).join("&");
  const workbenchApi = useApi<JournalWorkbenchResponse>(`/api/decision-workbench${workbenchQuery ? `?${workbenchQuery}` : ""}`);
  const observationApi = useApi<ObservationInboxResponse>("/api/observations?limit=100", {
    enabled: journalTab === "notes",
  });
  const subjects = listOf<SubjectAggregate>(workbenchApi.data, "subjects");
  const activeSubjects = useMemo(
    () => subjects.filter((item) => upper(item.subject?.status) !== "ARCHIVED"),
    [subjects],
  );
  const defaultSubjectId = text(workbenchApi.data?.selected_subject_id, "");
  const subjectId = requestedSubjectId || defaultSubjectId;
  const currentViewApi = useApi<Dict>(
    `/api/current-view?subject_id=${encodeURIComponent(subjectId)}`,
    { enabled: Boolean(subjectId) },
  );
  useAgentPageContext({
    surface: "decision-workbench",
    selected_subject_id: subjectId || null,
    workbench_subject_id: subjectId || null,
  });
  const partialFailures = listOf<string>(workbenchApi.data, "partial_failures");
  const researchUnavailable = partialFailures.includes("research_state");
  const monitorsUnavailable = partialFailures.includes("monitors");
  const agendaUnavailable = partialFailures.includes("agenda");
  const timelineUnavailable = partialFailures.includes("timeline");
  const accountsUnavailable = partialFailures.includes("accounts");
  const transactionsUnavailable = partialFailures.includes("transactions");
  const tradeCyclesUnavailable = partialFailures.includes("trade_cycles");
  const retroUnavailable = partialFailures.includes("retro");
  const scorecardsUnavailable = partialFailures.includes("scorecards");
  const reviewItemsUnavailable = partialFailures.includes("review_items");
  const allReviewItems = listOf<Dict>(workbenchApi.data, "review_items");
  const reviewItems = allReviewItems.filter((item) =>
    subjectFilters.length === 0 || subjectFilters.includes(text(item.subject_id, ""))
  );
  const reviewItemMetrics = asDict(workbenchApi.data?.review_item_metrics);
  const openReviewCount = openReviewCountFor(
    subjectFilters,
    reviewItems,
    reviewItemMetrics,
  );
  const behaviorReviewRuns = listOf<Dict>(workbenchApi.data, "behavior_review_runs");
  const observationData = observationApi.data?.data;
  const externalNotes = listOf<Dict>(observationData, "external_notes");
  const observationSources = listOf<Dict>(observationData, "observation_sources");
  const observationReviewWorkflowEnabled = observationData?.review_workflow_enabled !== false;
  const currentView = asDict(currentViewApi.data?.data);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const requested = query.get("subject_id");
    if (requested) {
      setRequestedSubjectId(requested);
      setSubjectFilters([requested]);
    }
    if (query.get("capture") === "decision") setCaptureRequested(true);
    const supersedes = query.get("supersedes_decision_id");
    if (supersedes?.startsWith("decision_")) setSupersedesDecisionId(supersedes);
  }, []);

  useEffect(() => {
    const update = () => {
      const value = window.location.hash.replace(/^#/, "") as JournalTab;
      if (JOURNAL_TABS.some((item) => item.id === value)) setJournalTab(value);
    };
    update();
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  useEffect(() => {
    function updateCyclePageSize() {
      setCyclePageSize(cyclePageSizeForViewport(window.innerWidth, window.innerHeight));
    }
    updateCyclePageSize();
    window.addEventListener("resize", updateCyclePageSize);
    return () => window.removeEventListener("resize", updateCyclePageSize);
  }, []);

  function selectJournalTab(value: JournalTab) {
    setJournalTab(value);
    window.history.replaceState(null, "", `#${value}`);
  }

  function updateSubjectFilters(values: string[]) {
    setSubjectFilters(values);
    setRequestedSubjectId(values.length === 1 ? values[0] : "");
  }

  function selectSubjectContext(value: string) {
    // Switching Decision context must not silently narrow the cross-note and
    // Review filters.  Explicit filter state remains visible and user-owned.
    setRequestedSubjectId(value);
  }

  useEffect(() => {
    if (workbenchApi.loading) return;
    if (activeSubjects.length === 0) {
      setRequestedSubjectId("");
      setSubjectFilters([]);
      return;
    }
    const validSubjectIds = new Set(
      activeSubjects.map((item) => text(item.subject?.subject_id, "")).filter(Boolean),
    );
    setSubjectFilters((current) => {
      const next = current.filter((value) => validSubjectIds.has(value));
      return next.length === current.length ? current : next;
    });
    if (requestedSubjectId && !activeSubjects.some(
      (item) => text(item.subject?.subject_id, "") === requestedSubjectId
    )) {
      setRequestedSubjectId("");
    }
  }, [activeSubjects, requestedSubjectId, workbenchApi.loading]);

  useEffect(() => {
    setInstrumentFilters([]);
    setSelectedCycleId("");
    setCycleOffset(0);
  }, [subjectId]);

  useEffect(() => {
    setSelectedCycleId("");
    setCycleOffset(0);
  }, [accountFilters, classificationFilters, cycleSortMode, cycleStatusFilters, instrumentFilters, periodFilter, qualityFilter]);

  useEffect(() => {
    setSelectedCycleId("");
    setCycleOffset(0);
  }, [cyclePageSize]);

  const selected = subjects.find((item) => text(item.subject?.subject_id, "") === subjectId);
  const subject = selected?.subject ?? {};
  const state = stateOf(selected);
  const theses = listOf<Dict>(state, "theses");
  const revisions = listOf<Dict>(state, "latest_revisions");
  const liveTheses = theses.filter((item) =>
    ["ACTIVE", "STRENGTHENED", "WEAKENED"].includes(upper(item.status))
  );
  const primaryThesis = liveTheses.find((item) => upper(item.role) === "PRIMARY")
    ?? liveTheses[0]
    ?? null;
  const primaryRevision = revisions.find(
    (item) => text(item.thesis_id, "") === text(primaryThesis?.thesis_id, ""),
  ) ?? null;
  const pendingCandidates = listOf<Dict>(state, "pending_candidates");
  const openQuestions = listOf<Dict>(state, "open_questions");
  const plan = currentTradePlan(state);
  const planId = text(plan?.plan_id, "");
  const planVersion = number(plan?.version);
  const planLinkReady = Boolean(planId && planVersion >= 1);
  const instrumentId = text(plan?.instrument_id, text(subject.primary_instrument_id, ""));
  const accountRows = listOf<Dict>(unwrap(asDict(workbenchApi.data?.accounts)), "accounts");
  const allPositions = accountRows.flatMap((account) =>
    listOf<Dict>(account, "positions")
      .map((position): Dict => ({ ...position, account_ref: account.account_ref, provider: account.provider })),
  );
  const allTransactions = listOf<Dict>(
    unwrap(asDict(workbenchApi.data?.transactions)),
    "transactions",
  );
  const subjectInstrumentFilters = activeSubjects
    .filter((item) => subjectFilters.includes(text(item.subject?.subject_id, "")))
    .map((item) => text(item.subject?.primary_instrument_id, ""))
    .filter(Boolean);
  const activeInstrumentFilters = selectedInstrumentScope(
    instrumentFilters,
    subjectInstrumentFilters,
  );
  const contextInstrumentFilters = contextInstrumentScope(
    activeInstrumentFilters,
    instrumentId,
  );
  const filteredExternalNotes = externalNotes.filter((item) =>
    activeInstrumentFilters.length === 0
      || activeInstrumentFilters.includes(text(asDict(item.identity).primary_instrument_id, ""))
  );
  const activePeriodStart = selectedPeriodStart ? Date.parse(selectedPeriodStart) : null;
  const activePeriodEnd = selectedPeriodEnd ? Date.parse(selectedPeriodEnd) : null;
  const relatedPositions = allPositions
    .filter((position) => contextInstrumentFilters.length === 0 || contextInstrumentFilters.includes(text(position.instrument_id, "")))
    .filter((position) => accountFilters.length === 0 || accountFilters.includes(text(position.account_ref, "")));
  const filteredTransactions = allTransactions
    .filter((item) => activeInstrumentFilters.length === 0 || activeInstrumentFilters.includes(text(item.instrument_id, "")))
    .filter((item) => accountFilters.length === 0 || accountFilters.includes(text(item.account_ref, "")))
    .filter((item) => activePeriodStart === null || (Date.parse(text(item.occurred_at, "")) || 0) >= activePeriodStart)
    .filter((item) => activePeriodEnd === null || (Date.parse(text(item.occurred_at, "")) || 0) <= activePeriodEnd)
    .sort((left, right) =>
      (Date.parse(text(right.occurred_at, "")) || 0)
      - (Date.parse(text(left.occurred_at, "")) || 0),
    );
  const relatedTransactions = allTransactions
    .filter((item) => contextInstrumentFilters.length === 0 || contextInstrumentFilters.includes(text(item.instrument_id, "")))
    .filter((item) => accountFilters.length === 0 || accountFilters.includes(text(item.account_ref, "")))
    .filter((item) => activePeriodStart === null || (Date.parse(text(item.occurred_at, "")) || 0) >= activePeriodStart)
    .filter((item) => activePeriodEnd === null || (Date.parse(text(item.occurred_at, "")) || 0) <= activePeriodEnd)
    .sort((left, right) =>
      (Date.parse(text(right.occurred_at, "")) || 0)
      - (Date.parse(text(left.occurred_at, "")) || 0),
    );
  const relatedOrderIntents = listOf<Dict>(workbenchApi.data, "order_intents")
    .filter((item) => activeInstrumentFilters.length === 0 || activeInstrumentFilters.includes(text(item.instrument_id, "")))
    .filter((item) => subjectFilters.length === 0 || !item.case_id || subjectFilters.includes(text(item.case_id, "")));
  const transactionWindowLimited = allTransactions.length >= 500;
  const latestTransaction = relatedTransactions[0] ?? null;
  const heldQuantity = relatedPositions.reduce(
    (total, position) => total + number(position.quantity),
    0,
  );
  const timelineItems = listOf<Dict>(
    unwrap(asDict(workbenchApi.data?.timeline)),
    "items",
  );
  const decisionItems = timelineItems.filter((item) => upper(item.entity_type) === "DECISION");
  const latestDecision = decisionItems[0] ?? null;
  const behaviorData = unwrap(asDict(workbenchApi.data?.behavior));
  const performanceSeries = listOf<Dict>(
    unwrap(asDict(workbenchApi.data?.performance_series)),
    "series",
  );
  const activityAnnotations = listOf<Dict>(workbenchApi.data, "activity_annotations");
  const annotationByTransaction = new Map(activityAnnotations.map((item) => [
    `${text(item.provider, "")}:${text(item.account_ref, "")}:${text(item.provider_transaction_id, "")}`,
    item,
  ]));
  const tradeCycleData = unwrap(asDict(workbenchApi.data?.trade_cycles));
  const allTradeCycles = listOf<Dict>(tradeCycleData, "cycles");
  const transactionTimeByActivity = new Map(allTransactions.map((transaction) => [
    `${text(transaction.account_ref, "")}:${text(transaction.provider_transaction_id, "")}`,
    Date.parse(text(transaction.occurred_at, "")) || 0,
  ]));
  function cycleLatestActivityTime(cycle: Dict): number {
    const accountRef = text(cycle.account_ref, "");
    const exactTimes = listOf<string>(cycle, "activity_ids")
      .map((activityId) => transactionTimeByActivity.get(`${accountRef}:${activityId}`) ?? 0)
      .filter((value) => value > 0);
    return exactTimes.length > 0
      ? Math.max(...exactTimes)
      : cycleReviewTime(cycle);
  }
  const overallCycleQuality = upper(tradeCycleData.status, "UNKNOWN");
  const incompleteCycleCount = allTradeCycles.filter((cycle) => upper(cycle.quality) === "INCOMPLETE").length;
  const unresolvedCycleCount = allTradeCycles.filter((cycle) => upper(cycle.status) === "UNRESOLVED").length;
  const cycleCandidates = allTradeCycles
    .filter((cycle) => activeInstrumentFilters.length === 0 || activeInstrumentFilters.includes(text(cycle.instrument_id, "")))
    .filter((cycle) => accountFilters.length === 0 || accountFilters.includes(text(cycle.account_ref, "")))
    .filter((cycle) => classificationFilters.length === 0 || classificationFilters.includes(upper(cycle.classification) as ActivityClassification))
    .filter((cycle) => activePeriodStart === null || cycleReviewTime(cycle) >= activePeriodStart)
    .filter((cycle) => activePeriodEnd === null || cycleReviewTime(cycle) <= activePeriodEnd);
  const cycleStatusCounts = Object.fromEntries(
    (["OPEN", "CLOSED", "UNRESOLVED"] as CycleStatusFilter[]).map((status) => [
      status,
      cycleCandidates.filter((cycle) => upper(cycle.status) === status).length,
    ]),
  ) as Record<CycleStatusFilter, number>;
  const cycleIncompleteCount = cycleCandidates.filter((cycle) => upper(cycle.quality) === "INCOMPLETE").length;
  const tradeCycles = cycleCandidates
    .filter((cycle) => cycleStatusFilters.length === 0 || cycleStatusFilters.includes(upper(cycle.status) as CycleStatusFilter))
    .filter((cycle) => qualityFilter === "ALL" || upper(cycle.quality) === qualityFilter)
    .sort((left, right) => {
      const instrumentOrder = text(left.instrument_id, "").localeCompare(text(right.instrument_id, ""), "en", { numeric: true });
      const latestOrder = cycleLatestActivityTime(left) - cycleLatestActivityTime(right);
      const openedOrder = (Date.parse(text(left.opened_at, "")) || 0) - (Date.parse(text(right.opened_at, "")) || 0);
      const comparison = cycleSortMode === "LATEST_ASC" ? latestOrder
        : cycleSortMode === "OPENED_DESC" ? -openedOrder
          : cycleSortMode === "OPENED_ASC" ? openedOrder
            : cycleSortMode === "INSTRUMENT_ASC" ? instrumentOrder
              : cycleSortMode === "INSTRUMENT_DESC" ? -instrumentOrder
                : -latestOrder;
      return comparison || -latestOrder || text(left.cycle_id, "").localeCompare(text(right.cycle_id, ""));
    });

  const subjectTradeCycles = tradeCycles.filter((cycle) =>
    contextInstrumentFilters.length === 0
      || contextInstrumentFilters.includes(text(cycle.instrument_id, ""))
  );
  const tradeCycleOverrides = listOf<Dict>(tradeCycleData, "override_revisions");
  const latestTradeCycle = subjectTradeCycles[0] ?? null;
  const latestCyclePnl = latestTradeCycle ? cyclePnlPresentation(latestTradeCycle) : null;
  const tradeCycleProjectionIncomplete = upper(tradeCycleData.status) !== "COMPLETE";
  const visibleTradeCycles = tradeCycles.slice(cycleOffset, cycleOffset + cyclePageSize);
  const selectedCycle = tradeCycles.find((cycle) => text(cycle.cycle_id, "") === selectedCycleId)
    ?? visibleTradeCycles[0]
    ?? null;
  const selectedCyclePnl = selectedCycle ? cyclePnlPresentation(selectedCycle) : null;
  const selectedCycleActivityIds = new Set(listOf<string>(selectedCycle, "activity_ids"));
  const selectedCycleTransactions = allTransactions.filter((transaction) =>
    selectedCycleActivityIds.has(text(transaction.provider_transaction_id, ""))
  );
  const closedPnlCycles = tradeCycles.filter((cycle) =>
    upper(cycle.status) === "CLOSED"
      && upper(cycle.classification) !== "CASH_MANAGEMENT"
      && cycle.net_realized_pnl != null
  );
  const wins = closedPnlCycles.filter((cycle) => number(cycle.net_realized_pnl) > 0);
  const losses = closedPnlCycles.filter((cycle) => number(cycle.net_realized_pnl) < 0);
  const grossWins = wins.reduce((total, cycle) => total + number(cycle.net_realized_pnl), 0);
  const grossLosses = Math.abs(losses.reduce((total, cycle) => total + number(cycle.net_realized_pnl), 0));
  const realizedPnl = grossWins - grossLosses;
  const averageWin = wins.length ? grossWins / wins.length : null;
  const averageLoss = losses.length ? grossLosses / losses.length : null;
  const payoffRatio = averageWin != null && averageLoss ? averageWin / averageLoss : null;
  const profitFactor = grossLosses ? grossWins / grossLosses : null;
  const winRate = closedPnlCycles.length ? wins.length / closedPnlCycles.length : null;
  const holdingDurations = closedPnlCycles
    .map((cycle) => number(cycle.holding_duration_seconds))
    .filter((value) => value > 0)
    .sort((left, right) => left - right);
  const medianHoldingDays = holdingDurations.length
    ? holdingDurations[Math.floor(holdingDurations.length / 2)] / 86_400
    : null;
  const accountOptions = Array.from(new Set([
    ...accountRows.map((account) => text(account.account_ref, "")),
    ...allTradeCycles.map((cycle) => text(cycle.account_ref, "")),
  ].filter(Boolean))).sort();
  const instrumentOptions = Array.from(new Set([
    ...allTradeCycles.map((cycle) => text(cycle.instrument_id, "")),
    ...allTransactions.map((transaction) => text(transaction.instrument_id, "")),
  ].filter(Boolean))).sort();
  const accountAutosuggestOptions: AutosuggestOption[] = accountOptions.map((value) => {
    const account = accountRows.find((item) => text(item.account_ref, "") === value);
    return {
      value,
      label: `${upper(account?.provider, "ACCOUNT")} · ${shortId(value)}`,
      description: `${listOf<Dict>(account, "positions").length} durable position${listOf<Dict>(account, "positions").length === 1 ? "" : "s"}`,
    };
  });
  const instrumentAutosuggestOptions: AutosuggestOption[] = instrumentOptions.map((value) => ({
    value,
    label: shortId(value),
    description: value,
  }));
  const subjectAutosuggestOptions: AutosuggestOption[] = activeSubjects.map((item) => ({
    value: text(item.subject?.subject_id, ""),
    label: text(item.subject?.title, "Untitled Research Subject"),
    description: `${upper(item.subject?.subject_type, "RESEARCH SUBJECT")} · ${shortId(item.subject?.primary_instrument_id)}`,
  })).filter((option) => option.value);
  const holdingBuckets = [
    { label: "Same Day", minimum: 0, maximum: 1 },
    { label: "1–5 Days", minimum: 1, maximum: 5 },
    { label: "5–20 Days", minimum: 5, maximum: 20 },
    { label: "20+ Days", minimum: 20, maximum: Number.POSITIVE_INFINITY },
  ].map((bucket) => {
    const cycles = closedPnlCycles.filter((cycle) => {
      const days = number(cycle.holding_duration_seconds) / 86_400;
      return days >= bucket.minimum && days < bucket.maximum;
    });
    return {
      ...bucket,
      count: cycles.length,
      pnl: cycles.reduce((total, cycle) => total + number(cycle.net_realized_pnl), 0),
    };
  });
  const cycleStatsByInstrument = tradeCycles.reduce((groups, cycle) => {
    if (upper(cycle.status) !== "CLOSED") return groups;
    const instrument = text(cycle.instrument_id, "");
    if (!instrument) return groups;
    const current = groups.get(instrument) ?? { closedCycles: 0, knownPnl: 0, pnlCycles: 0 };
    current.closedCycles += 1;
    const knownPnl = cycle.net_realized_pnl ?? cycle.gross_realized_pnl;
    if (knownPnl != null) {
      current.knownPnl += number(knownPnl);
      current.pnlCycles += 1;
    }
    groups.set(instrument, current);
    return groups;
  }, new Map<string, { closedCycles: number; knownPnl: number; pnlCycles: number }>());
  const tradedInstrumentRows: InstrumentTradeRow[] = Array.from(filteredTransactions
    .filter((transaction) => upper(transaction.kind) === "TRADE" && text(transaction.instrument_id, ""))
    .reduce((groups, transaction) => {
      const instrument = text(transaction.instrument_id, "");
      const occurredAt = text(transaction.occurred_at, "");
      const current = groups.get(instrument) ?? {
        instrument,
        fills: 0,
        bought: 0,
        sold: 0,
        accountRefs: new Set<string>(),
        firstTradeAt: occurredAt,
        lastTradeAt: occurredAt,
      };
      current.fills += 1;
      if (upper(transaction.side) === "BUY") current.bought += number(transaction.quantity);
      if (upper(transaction.side) === "SELL") current.sold += number(transaction.quantity);
      const accountRef = text(transaction.account_ref, "");
      if (accountRef) current.accountRefs.add(accountRef);
      if ((Date.parse(occurredAt) || 0) < (Date.parse(current.firstTradeAt) || 0)) current.firstTradeAt = occurredAt;
      if ((Date.parse(occurredAt) || 0) > (Date.parse(current.lastTradeAt) || 0)) current.lastTradeAt = occurredAt;
      groups.set(instrument, current);
      return groups;
    }, new Map<string, {
      instrument: string;
      fills: number;
      bought: number;
      sold: number;
      accountRefs: Set<string>;
      firstTradeAt: string;
      lastTradeAt: string;
    }>()).values())
    .map((row) => {
      const cycleStats = cycleStatsByInstrument.get(row.instrument);
      return {
        instrument: row.instrument,
        fills: row.fills,
        bought: row.bought,
        sold: row.sold,
        accounts: row.accountRefs.size,
        closedCycles: cycleStats?.closedCycles ?? 0,
        knownPnl: cycleStats?.knownPnl ?? 0,
        pnlCycles: cycleStats?.pnlCycles ?? 0,
        firstTradeAt: row.firstTradeAt,
        lastTradeAt: row.lastTradeAt,
      };
    });
  const instrumentTableOptions: AutosuggestOption[] = tradedInstrumentRows
    .map((row) => ({
      value: row.instrument,
      label: shortId(row.instrument),
      description: `${row.instrument} · ${row.fills} fill${row.fills === 1 ? "" : "s"}`,
    }))
    .sort((left, right) => left.label.localeCompare(right.label, "en", { numeric: true }));
  const filteredInstrumentRows = tradedInstrumentRows
    .filter((row) => instrumentTableFilters.length === 0 || instrumentTableFilters.includes(row.instrument))
    .sort((left, right) => {
      const key = instrumentTableSort.key;
      const comparison = key === "instrument"
        ? left.instrument.localeCompare(right.instrument, "en", { numeric: true, sensitivity: "base" })
        : key === "lastTradeAt"
          ? (Date.parse(left.lastTradeAt) || 0) - (Date.parse(right.lastTradeAt) || 0)
          : left[key] - right[key];
      return (instrumentTableSort.direction === "asc" ? comparison : -comparison)
        || left.instrument.localeCompare(right.instrument, "en", { numeric: true });
    });
  const visibleInstrumentRows = filteredInstrumentRows.slice(
    instrumentTableOffset,
    instrumentTableOffset + INSTRUMENT_TABLE_PAGE_SIZE,
  );
  const visibleInstrumentFillCount = filteredInstrumentRows.reduce(
    (total, row) => total + row.fills,
    0,
  );
  const journalTimelineRows = [
    ...timelineItems.map((item) => ({
      key: text(item.entity_id),
      kind: upper(item.entity_type),
      title: text(item.title),
      detail: text(item.summary),
      occurredAt: text(item.occurred_at, ""),
    })),
    ...filteredTransactions.map((item) => {
      const annotation = annotationByTransaction.get(
        `${text(item.provider, "")}:${text(item.account_ref, "")}:${text(item.provider_transaction_id, "")}`,
      );
      return {
        key: text(item.provider_transaction_id),
        kind: upper(item.kind),
        title: `${upper(item.side, upper(item.kind))} · ${shortId(item.instrument_id)}`,
        detail: `${text(item.quantity, "—")} @ ${text(item.price, "—")} ${text(item.currency, "")} · ${text(annotation?.status, "Unlinked")} · ${text(annotation?.classification, "Unclassified")} · ${annotation?.order_intent_id ? "Exact order link" : "No order link"}`,
        occurredAt: text(item.occurred_at, ""),
      };
    }),
    ...relatedOrderIntents.map((item) => ({
      key: text(item.order_intent_id),
      kind: `ORDER ${upper(item.status)}`,
      title: `${upper(item.instruction)} · ${shortId(item.instrument_id)}`,
      detail: `${text(item.quantity)} · ${text(item.order_type)}${item.limit_price == null ? "" : ` @ ${text(item.limit_price)}`} · ${item.broker_order_id ? "Broker accepted" : "No Broker order ID"} · ${item.decision_id || item.trade_plan_id ? "Exact research link" : "Intent unlinked"}`,
      occurredAt: text(item.submitted_at, text(item.updated_at, text(item.created_at, ""))),
    })),
  ].sort(
    (left, right) =>
      (Date.parse(right.occurredAt) || 0) - (Date.parse(left.occurredAt) || 0),
  );
  const visibleTimelineRows = journalTimelineRows.slice(
    timelineOffset,
    timelineOffset + TIMELINE_PAGE_SIZE,
  );

  useEffect(() => {
    setTimelineOffset(0);
  }, [accountFilters, instrumentFilters, periodFilter, subjectFilters]);

  useEffect(() => {
    if (timelineOffset >= journalTimelineRows.length && timelineOffset !== 0) {
      setTimelineOffset(0);
    }
  }, [journalTimelineRows.length, timelineOffset]);

  useEffect(() => {
    if (cycleOffset >= tradeCycles.length && cycleOffset !== 0) setCycleOffset(0);
  }, [cycleOffset, tradeCycles.length]);

  useEffect(() => {
    setInstrumentTableOffset(0);
  }, [instrumentTableFilters, instrumentTableSort.key, instrumentTableSort.direction]);

  useEffect(() => {
    if (instrumentTableOffset >= filteredInstrumentRows.length && instrumentTableOffset !== 0) {
      setInstrumentTableOffset(0);
    }
  }, [filteredInstrumentRows.length, instrumentTableOffset]);

  function changeInstrumentTableSort(key: InstrumentTableSortKey) {
    setInstrumentTableSort((current) => ({
      key,
      direction: current.key === key && current.direction === "desc" ? "asc" : "desc",
    }));
  }

  useEffect(() => {
    if (!captureRequested || !selected) return;
    setCaptureContextError(null);
    setDecisionError(null);
    setDecisionSourceNote(null);
    setDecisionSourceRevisionId(null);
    setDecisionSourceReview(null);
    setDecisionReviewPackage(null);
    setDecisionDraftScenarios([]);
    setDecisionOpen(true);
    setCaptureRequested(false);
    const url = new URL(window.location.href);
    url.searchParams.delete("capture");
    url.searchParams.delete("supersedes_decision_id");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, [captureRequested, selected]);

  useEffect(() => {
    if (!captureRequested || workbenchApi.loading || selected) return;
    setCaptureRequested(false);
    setCaptureContextError(
      requestedSubjectId
        ? "The requested Research Subject is unavailable or archived. Open Research and choose an available Subject before recording a Decision."
        : "Choose a Research Subject before recording a Decision.",
    );
  }, [captureRequested, requestedSubjectId, selected, workbenchApi.loading]);

  async function prepareWeeklyReview() {
    setBehaviorReviewBusy(true); setBehaviorReviewError(null);
    try {
      const schedule = asDict(await getJson("/api/retro"));
      const windows = asDict(schedule.console_windows);
      const previousWindow = asDict(windows.previous);
      const nextWindow = asDict(windows.next);
      const start = new Date(text(previousWindow.start, ""));
      const end = new Date(text(previousWindow.end, ""));
      const nextStart = new Date(text(nextWindow.start, ""));
      const nextEnd = new Date(text(nextWindow.end, ""));
      if ([start, end, nextStart, nextEnd].some((value) => Number.isNaN(value.getTime()))) {
        throw new Error("The canonical weekly review window is unavailable.");
      }
      setWeeklyReviewPreview({
        start: start.toISOString(),
        end: end.toISOString(),
        nextStart: nextStart.toISOString(),
        nextEnd: nextEnd.toISOString(),
      });
    } catch (cause) {
      setBehaviorReviewError(cause instanceof Error ? cause.message : "Behavior Review preview failed");
    } finally { setBehaviorReviewBusy(false); }
  }

  async function createWeeklyReview() {
    if (!weeklyReviewPreview) return;
    setBehaviorReviewBusy(true); setBehaviorReviewError(null);
    const start = new Date(weeklyReviewPreview.start);
    const end = new Date(weeklyReviewPreview.end);
    const nextStart = new Date(weeklyReviewPreview.nextStart);
    const nextEnd = new Date(weeklyReviewPreview.nextEnd);
    try {
      const retroResponse = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "research_workflow_run",
        arguments: { request: {
          operation: "trade_retro",
          action: "run",
          start: start.toISOString(),
          end: end.toISOString(),
          use_llm: false,
          idempotency_key: `console-journal-retro-run-${start.toISOString().slice(0, 10)}`,
        } },
        confirmation: "research_workflow_run",
        preserve_full_result: true,
      });
      const retroEnvelope = asDict(retroResponse.result);
      if (retroEnvelope.ok !== true) {
        const firstError = listOf<Dict>(retroEnvelope, "errors")[0];
        throw new Error(text(firstError?.message, "The immutable period review could not be generated."));
      }
      const actionItems = listOf<string>(retroReview, "action_items").map((actionText) => ({
        action_text: actionText,
        review_item_source_keys: reviewItems.map((item) => text(item.source_key, "")).filter(Boolean),
        retro_review_ids: retroReview.review_id ? [text(retroReview.review_id)] : [],
        cycle_ids: tradeCycles.map((item) => text(item.cycle_id)),
        decision_ids: decisionItems.map((item) => text(item.entity_id)),
      }));
      await postApi<Dict>("/api/behavior-reviews", {
        period_kind: "WEEKLY",
        period_start: start.toISOString(),
        period_end: end.toISOString(),
        strategy_code: "strategy_v1",
        instrument_ids: activeInstrumentFilters,
        cycle_ids: tradeCycles.map((item) => text(item.cycle_id)),
        decision_ids: decisionItems.map((item) => text(item.entity_id)),
        retro_run_ids: latestRetro?.run_id ? [text(latestRetro.run_id)] : [],
        retro_review_ids: retroReview.review_id ? [text(retroReview.review_id)] : [],
        review_item_source_keys: reviewItems.map((item) => text(item.source_key, "")).filter(Boolean),
        subject_ids: subjectId ? [subjectId] : [],
        action_items: actionItems,
        source_read_complete: !retroUnavailable && !reviewItemsUnavailable,
        source_error_code: retroUnavailable || reviewItemsUnavailable ? "BEHAVIOR_REVIEW_SOURCE_UNAVAILABLE" : null,
        idempotency_key: `console-behavior-review-${subjectId || "global"}-${start.toISOString().slice(0, 10)}`,
      });
      await postApi<Dict>("/api/tools/invoke", {
        tool_name: "research_workflow_run",
        arguments: { request: {
          operation: "trade_retro",
          action: "prepare",
          start: nextStart.toISOString(),
          end: nextEnd.toISOString(),
          idempotency_key: `console-journal-retro-prepare-${nextStart.toISOString().slice(0, 10)}`,
        } },
        confirmation: "research_workflow_run",
        preserve_full_result: true,
      });
      setWeeklyReviewPreview(null);
      workbenchApi.refresh();
    } catch (cause) {
      setBehaviorReviewError(cause instanceof Error ? cause.message : "Behavior Review failed");
    } finally { setBehaviorReviewBusy(false); }
  }

  const linkedMonitors = monitorItems(workbenchApi.data).filter(
    (item) => text(asDict(item.monitor).subject_id, "") === subjectId,
  );
  const activeMonitors = linkedMonitors.filter(
    (item) => upper(asDict(item.monitor).status) === "ACTIVE",
  );
  const triggeredRules = linkedMonitors
    .flatMap((item) => listOf<Dict>(item, "rule_states"))
    .filter((item) => upper(item.state) === "TRIGGERED");
  const latestMonitorRun = latestBy(
    linkedMonitors
      .map((item) => asDict(item.latest_run))
      .filter((item) => Object.keys(item).length > 0),
    "completed_at",
  );

  const catalysts = agendaItems(workbenchApi.data);
  const overdueCatalysts = catalysts.filter((item) =>
    listOf<string>(item, "limitation_codes").includes("AGENDA_OUTCOME_UNVERIFIED")
  );
  const upcomingCatalysts = catalysts.filter(
    (item) => upper(item.status) === "UPCOMING" && !overdueCatalysts.includes(item),
  );

  const cards = scorecardRuns(workbenchApi.data);
  const latestScorecard = latestBy(cards, "generated_at");
  const scorecardGaps = listOf<Dict>(latestScorecard, "dimensions").filter(
    (item) => upper(item.status) !== "EVALUATED"
      || ["FAIL", "PARTIAL", "NOT_EVALUATED"].includes(upper(item.result_code)),
  );

  const allRetroRuns = retroRuns(workbenchApi.data);
  const relatedRetro = allRetroRuns.filter((run) => {
    if (!subjectId) return true;
    const snapshotSubjectIds = listOf<string>(run, "subject_ids");
    if (snapshotSubjectIds.length > 0) return snapshotSubjectIds.includes(subjectId);
    return listOf<Dict>(run, "findings").some(
      (finding) => (planId && text(finding.plan_id, "") === planId)
        || (instrumentId && text(finding.instrument_id, "") === instrumentId),
    );
  });
  const latestRetro = latestBy(relatedRetro, "generated_at");
  const retroReview = asDict(latestRetro?.latest_review);
  const retroReviewStatus = Object.keys(retroReview).length
    ? upper(retroReview.status)
    : "UNREVIEWED";

  const nextSteps: NextStep[] = [];
  if (!researchUnavailable && upper(subject.status) === "ACTIVE" && liveTheses.length === 0) {
    nextSteps.push({ key: "thesis", severity: "ACTION", title: "Create or activate a falsifiable Thesis", detail: "The Research Subject is tracked but has no live judgment.", href: `/research#subject-${subjectId}` });
  }
  if (pendingCandidates.length > 0) {
    nextSteps.push({ key: "candidates", severity: "REVIEW", title: `${pendingCandidates.length} candidate(s) await review`, detail: "Confirm, reject, or withdraw each exact proposal.", href: `/research#subject-${subjectId}` });
  }
  if (liveTheses.length > 0 && !plan) {
    nextSteps.push({ key: "plan", severity: "GAP", title: "Translate the live Thesis into a Trade Plan", detail: "Execution intent and review conditions are not yet durable.", href: `/research#subject-${subjectId}` });
  }
  if (!monitorsUnavailable && plan && activeMonitors.length === 0) {
    nextSteps.push({ key: "monitor", severity: "GAP", title: "No active Monitor covers this plan", detail: "Bind monitorable conditions without changing the plan or position.", href: "/monitors" });
  }
  if (reviewItemsUnavailable && overdueCatalysts.length > 0) {
    nextSteps.push({ key: "catalyst", severity: "ATTENTION", title: `${overdueCatalysts.length} Catalyst outcome(s) overdue`, detail: "Link a durable Event, Report, or Evidence fact, or revise the expected window.", href: "/agenda#agenda-detail" });
  }
  if (reviewItemsUnavailable && latestRetro && ["UNREVIEWED", "OPEN", "DISPUTED"].includes(retroReviewStatus)) {
    nextSteps.push({ key: "retro", severity: "REVIEW", title: `Period review is ${retroReviewStatus.toLowerCase()}`, detail: "Review deterministic findings and record follow-up actions.", href: "#reviews" });
  }
  if (reviewItemsUnavailable && latestScorecard && scorecardGaps.length > 0) {
    nextSteps.push({ key: "scorecard", severity: "GAP", title: `${scorecardGaps.length} Scorecard dimension gap(s)`, detail: "Inspect evidence quality and repeated discipline gaps before revising judgment.", href: `/scorecards?subject_id=${encodeURIComponent(subjectId)}` });
  }
  if (!reviewItemsUnavailable) {
    nextSteps.push(...reviewItems.map((item) => ({
      key: text(item.source_key, text(item.review_item_id)),
      severity: text(item.severity, "ATTENTION"),
      title: journalReviewTitle(item.title),
      detail: `${text(item.detail, "Inspect the durable source.")} · ${upper(item.status)}`,
      href: text(item.href, "/"),
      reviewItem: item,
    })));
  }
  if (partialFailures.length > 0) {
    nextSteps.unshift({ key: "incomplete-context", severity: "UNAVAILABLE", title: "Decision context is incomplete", detail: `Retry before interpreting missing sections: ${partialFailures.join(", ")}.`, href: "#decision-context-status" });
  }
  const compactNextSteps = Array.from(
    nextSteps.reduce((groups, item) => {
      const key = item.reviewItem
        ? `${item.severity}\0${item.title}`
        : `${item.severity}\0${item.title}\0${item.href}`;
      const existing = groups.get(key);
      if (existing) existing.push(item);
      else groups.set(key, [item]);
      return groups;
    }, new Map<string, NextStep[]>()),
  ).map(([key, items]) => items.length === 1 ? items[0] : ({
    key: `group-${key}`,
    severity: items[0].severity,
    title: `${items[0].title} · ${items.length} Items`,
    detail: `${items[0].detail} Open the Reviews tab to process each exact item.`,
    href: "#reviews",
  } satisfies NextStep));
  const reviewPageSize = 12;
  const visibleReviewSteps = nextSteps.slice(reviewOffset, reviewOffset + reviewPageSize);
  useEffect(() => {
    if (reviewOffset >= nextSteps.length && reviewOffset !== 0) setReviewOffset(0);
  }, [nextSteps.length, reviewOffset]);

  function openReviewInput(kind: ReviewInput["kind"], item: Dict, status: ReviewInput["status"]) {
    setReviewInputValue("");
    setReviewInputError(null);
    setReviewInput({ kind, item, status });
  }

  async function persistReviewItem(item: Dict, status: "ACKNOWLEDGED" | "RESOLVED", resolutionNote?: string, dueAt?: string) {
    const reviewItemId = text(item.review_item_id, "");
    if (!reviewItemId) return;
    setReviewBusy(reviewItemId);
    setReviewError(null);
    try {
      await postApi(`/api/review-items/${encodeURIComponent(reviewItemId)}/transition`, {
        status,
        expected_version: Number(item.version),
        resolution_note: resolutionNote,
        due_at: dueAt,
        idempotency_key: `workbench-review-${reviewItemId}-${status.toLowerCase()}-${crypto.randomUUID()}`,
        authorization_note: `User explicitly selected ${status.toLowerCase()} in Workbench.`,
        confirmation: "review_item_update",
      });
      workbenchApi.refresh();
    } catch (cause) {
      setReviewError(cause instanceof Error ? cause.message : "Review item update failed");
    } finally {
      setReviewBusy(null);
    }
  }

  function transitionReviewItem(item: Dict, status: "ACKNOWLEDGED" | "RESOLVED") {
    if (status === "RESOLVED") {
      openReviewInput("resolution", item, status);
      return;
    }
    if (upper(item.status) === "ACKNOWLEDGED") {
      openReviewInput("due", item, status);
      return;
    }
    setReviewAcknowledgement(item);
  }

  function submitReviewInput(value: string) {
    if (!reviewInput) return;
    const normalized = value.trim();
    if (reviewInput.kind === "resolution") {
      if (!normalized) {
        setReviewInputError("Resolution Note is required.");
        return;
      }
      const { item, status } = reviewInput;
      setReviewInput(null);
      void persistReviewItem(item, status, normalized);
      return;
    }
    const dueAt = normalized ? endOfDayIsoOrNull(normalized) : undefined;
    if (normalized && !dueAt) {
      setReviewInputError("Due Date must use YYYY-MM-DD.");
      return;
    }
    const { item, status } = reviewInput;
    setReviewInput(null);
    void persistReviewItem(item, status, undefined, dueAt ?? undefined);
  }

  async function saveDecision() {
    const reason = decisionReason.trim();
    if (!reason) {
      setDecisionError("Reason is required.");
      return;
    }
    if (
      decisionScenario === "INVALIDATION"
      && ["initiate_intent", "add_intent", "hold"].includes(decisionAction)
    ) {
      setDecisionError(
        "Invalidation cannot initiate, add, or hold under strategy_v1. Reduce, exit, avoid, or create a genuinely new Plan.",
      );
      return;
    }
    if (["initiate_intent", "add_intent"].includes(decisionAction) && !planLinkReady) {
      setDecisionError(
        "Initiate or Add requires an exact current Trade Plan. Create or confirm the Plan in Research first.",
      );
      return;
    }
    if (!subjectId) {
      setDecisionError("Select a Research Subject first.");
      return;
    }
    setDecisionBusy(true);
    setDecisionError(null);
    setDecisionMessage(null);
    const selectedAction = decisionAction.toUpperCase();
    const reviewDueAt = decisionReviewDate ? endOfDayIsoOrNull(decisionReviewDate) : null;
    if (decisionReviewDate && reviewDueAt === null) {
      setDecisionBusy(false);
      setDecisionError("Review Date must be a valid date.");
      return;
    }
    const scenarioLines = DECISION_SCENARIOS.map((scenario) => (
      scenario === decisionScenario
        ? `${scenario}: ${selectedAction} · ${reason}`
        : `${scenario}: REVIEW · Follow the current confirmed Trade Plan; this record authorizes no new action for that scenario.`
    ));
    const rationale = [
      "Strategy: strategy_v1",
      `Current Scenario: ${decisionScenario}`,
      `Action: ${selectedAction}`,
      `Reason: ${reason}`,
      `Review Due: ${decisionReviewDate || "Not Set"}`,
      "",
      ...scenarioLines,
      ...(decisionSourceNote ? ["", `Source Note Revision: ${decisionSourceNote}`] : []),
      "",
      "This Decision Record does not submit, modify, or authorize an order.",
    ].join("\n");
    const revisionId = text(primaryRevision?.revision_id, "");
    try {
      const response = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "research_memory_append",
        arguments: {
          request: {
            operation: "decision",
            case_id: subjectId,
            decision_type: decisionAction,
            title: `${decisionScenario} · ${selectedAction.replaceAll("_", " ")}`,
            rationale,
            decided_at: new Date().toISOString(),
            decided_by: "user",
            confirmation_mode: ["watch", "no_action", "research_more"].includes(decisionAction)
              ? "normal"
              : "strict_review",
            strategy_code: "strategy_v1",
            strategy_version: null,
            scenario: decisionScenario,
            trade_plan_id: planLinkReady ? planId : null,
            trade_plan_version: planLinkReady ? planVersion : null,
            review_due_at: reviewDueAt,
            primary_instrument_id: instrumentId || null,
            thesis_revision_ids: revisionId ? [revisionId] : [],
            evidence_ids: [],
            report_ids: [],
            supersedes_decision_id: supersedesDecisionId,
            position_context_snapshot_id: null,
            external_note_revision_id: decisionSourceRevisionId,
            idempotency_key: `console-journal-decision-${crypto.randomUUID()}`,
          },
        },
        confirmation: "research_memory_append",
        preserve_full_result: true,
      });
      const result = asDict(response.result);
      if (result.ok !== true) {
        const firstError = listOf<Dict>(result, "errors")[0];
        throw new Error(text(firstError?.message, "Decision Record was rejected."));
      }
      const decision = asDict(result.data);
      const decisionId = text(decision.decision_id, "");
      let reviewClosureWarning: string | null = null;
      if (decisionSourceReview && decisionSourceRevisionId) {
        if (!decisionId) {
          reviewClosureWarning = "Decision recorded, but its Observation review could not be linked because the write response omitted the Decision identity.";
        } else {
          try {
            await postApi<Dict>(
              `/api/observation-reviews/${encodeURIComponent(decisionSourceReview.reviewId)}`,
              {
                status: decisionAction === "no_action" ? "NO_ACTION" : "ADOPTED",
                expected_version: decisionSourceReview.version,
                subject_id: subjectId,
                decision_id: decisionId,
                authorization_note: `User confirmed this exact Observation revision as Decision ${decisionId}.`,
                idempotency_key: `console-observation-review-${decisionSourceReview.reviewId}-${decisionSourceReview.version}-${decisionId}`,
                confirmation: "observation_review_update",
              },
            );
          } catch {
            reviewClosureWarning = "Decision recorded, but closing the Observation review failed. Refresh and repair the review link; do not duplicate the Decision.";
          }
        }
      }
      setDecisionOpen(false);
      setDecisionReason("");
      setDecisionSourceNote(null);
      setDecisionSourceRevisionId(null);
      setDecisionSourceReview(null);
      setDecisionReviewPackage(null);
      setDecisionDraftScenarios([]);
      setDecisionReviewDate(futureDateInput(7));
      setSupersedesDecisionId(null);
      setDecisionMessage(reviewClosureWarning ?? `${decisionScenario} · ${selectedAction.replaceAll("_", " ")} recorded.`);
      workbenchApi.refresh();
    } catch (cause) {
      setDecisionError(cause instanceof Error ? cause.message : "Decision Record failed.");
    } finally {
      setDecisionBusy(false);
    }
  }

  function primeNoteDecision(item: Dict) {
    const identity = asDict(item.identity);
    const revision = asDict(item.revision);
    const interpretation = asDict(item.interpretation);
    const payload = asDict(interpretation.payload);
    const scenarios = listOf<Dict>(payload, "user_scenarios");
    const sideways = scenarios.find((scenario) => upper(scenario.scenario) === "SIDEWAYS");
    const reason = text(
      payload.material_change_summary,
      text(revision.summary, "Review imported note."),
    );
    setDecisionScenario("SIDEWAYS");
    setDecisionAction("no_action");
    setDecisionReason(reason);
    setDecisionSourceNote(
      `${text(identity.title, text(revision.title, "Source Note"))} · v${number(revision.version) || 1}`,
    );
    setDecisionSourceRevisionId(text(revision.note_revision_id, "") || null);
    const review = asDict(item.review);
    const reviewId = text(review.review_id, "");
    const reviewVersion = number(review.version);
    setDecisionSourceReview(
      reviewId && reviewVersion >= 1 ? { reviewId, version: reviewVersion } : null,
    );
    setDecisionDraftScenarios(scenarios);
    setDecisionError(null);
    setSupersedesDecisionId(null);
    setDecisionOpen(true);
    if (sideways && ["HOLD", "NO_ACTION"].includes(upper(sideways.action))) {
      setDecisionAction(upper(sideways.action) === "HOLD" ? "hold" : "no_action");
    }
  }

  async function reviewNoteAsDecision(item: Dict, matchingSubjectId: string | null) {
    if (!matchingSubjectId) {
      setDecisionError("Create or select a matching Research Subject before reviewing this Note as a Decision.");
      return;
    }
    const revision = asDict(item.revision);
    const revisionId = text(revision.note_revision_id, "");
    let reviewItem = item;
    let reviewPackage: Dict | null = null;
    if (revisionId && observationReviewWorkflowEnabled) {
      setNoteReviewBusyId(revisionId);
      try {
        const response = await postApi<Dict>(
          `/api/observations/${encodeURIComponent(revisionId)}/review/ensure`,
          { subject_id: matchingSubjectId },
        );
        reviewItem = { ...item, review: asDict(response.data) };
        const packageResponse = asDict(
          await getJson(
            `/api/observations/${encodeURIComponent(revisionId)}/review`,
          ),
        );
        reviewPackage = asDict(packageResponse.data);
        if (reviewPackage.requires_deep_review === true) {
          await postApi<Dict>(
            `/api/observations/${encodeURIComponent(revisionId)}/deep-review`,
            { force: false },
          );
          const refreshedPackage = asDict(
            await getJson(
              `/api/observations/${encodeURIComponent(revisionId)}/review`,
            ),
          );
          reviewPackage = asDict(refreshedPackage.data);
          const deepReview = asDict(reviewPackage.deep_review);
          const deepPayload = asDict(deepReview.payload);
          if (upper(deepReview.status) === "SUCCEEDED" && Object.keys(deepPayload).length > 0) {
            reviewItem = {
              ...reviewItem,
              interpretation: {
                ...asDict(reviewItem.interpretation),
                payload: deepPayload,
                model: deepReview.model,
                review_layer: "DEEP",
              },
            };
          }
        }
      } catch (cause) {
        setNoteSyncError(
          cause instanceof Error
            ? cause.message
            : "Observation review could not be prepared.",
        );
        return;
      } finally {
        setNoteReviewBusyId(null);
      }
    }
    setDecisionReviewPackage(reviewPackage);
    if (matchingSubjectId !== subjectId || !selected) {
      setPendingNoteDecision(reviewItem);
      selectSubjectContext(matchingSubjectId);
      return;
    }
    primeNoteDecision(reviewItem);
  }

  async function deferNoteReview(item: Dict, matchingSubjectId: string | null) {
    const revision = asDict(item.revision);
    const revisionId = text(revision.note_revision_id, "");
    if (!revisionId) {
      setNoteSyncError("The exact Observation revision is unavailable.");
      return;
    }
    try {
      const response = await postApi<Dict>(
        `/api/observations/${encodeURIComponent(revisionId)}/review/ensure`,
        { subject_id: matchingSubjectId },
      );
      const review = asDict(response.data);
      const reviewId = text(review.review_id, "");
      const version = number(review.version);
      if (!reviewId || version < 1) throw new Error("Observation review identity is unavailable.");
      setObservationDefer({
        reviewId,
        version,
        title: text(asDict(item.identity).title, "View change"),
        dueDate: futureDateInput(7),
      });
    } catch (cause) {
      setNoteSyncError(cause instanceof Error ? cause.message : "Observation review could not be prepared.");
    }
  }

  async function confirmObservationDefer() {
    if (!observationDefer) return;
    const dueAt = endOfDayIsoOrNull(observationDefer.dueDate);
    if (!dueAt) {
      setNoteSyncError("Review Date must be a valid date.");
      return;
    }
    setObservationDeferBusy(true);
    try {
      await postApi<Dict>(
        `/api/observation-reviews/${encodeURIComponent(observationDefer.reviewId)}`,
        {
          status: "DEFERRED",
          expected_version: observationDefer.version,
          due_at: dueAt,
          authorization_note: `User deferred review until ${observationDefer.dueDate}.`,
          idempotency_key: `console-observation-defer-${observationDefer.reviewId}-${observationDefer.version}-${observationDefer.dueDate}`,
          confirmation: "observation_review_update",
        },
      );
      setObservationDefer(null);
      setNoteSyncMessage("View review deferred with a durable due date.");
      observationApi.refresh();
      workbenchApi.refresh();
    } catch (cause) {
      setNoteSyncError(cause instanceof Error ? cause.message : "Observation review could not be deferred.");
    } finally {
      setObservationDeferBusy(false);
    }
  }

  useEffect(() => {
    if (!pendingNoteDecision || !selected || text(subject.subject_id, "") !== subjectId) return;
    primeNoteDecision(pendingNoteDecision);
    setPendingNoteDecision(null);
  }, [pendingNoteDecision, selected, subject, subjectId]);

  async function refreshObservationSources() {
    setNoteSyncBusy(true);
    setNoteSyncError(null);
    setNoteSyncMessage(null);
    try {
      const result = await postApi<Dict>("/api/observations/sync", { source_code: null, analyze: false });
      const receipt = asDict(result.data);
      setNoteSyncMessage(
        `${text(receipt.notes_seen, "0")} note(s) scanned · ${text(receipt.revisions_created, "0")} revision(s) added${receipt.analysis_started === true ? " · background analysis started" : ""}.`,
      );
      observationApi.refresh();
    } catch (cause) {
      setNoteSyncError(cause instanceof Error ? cause.message : "Observation source sync failed.");
    } finally {
      setNoteSyncBusy(false);
    }
  }

  async function analyzeObservation(noteRevisionId: string) {
    setNoteAnalysisBusyId(noteRevisionId);
    setNoteSyncError(null);
    setNoteSyncMessage(null);
    try {
      const started = await postApi<Dict>(
        `/api/observations/${encodeURIComponent(noteRevisionId)}/analyze`,
        { retry_failed: true },
      );
      if (asDict(started.data).analysis_started !== true) {
        setNoteSyncMessage("Another observation analysis is already running. Retry this note after it finishes.");
        return;
      }
      setNoteSyncMessage("Analysis started in the background.");
      for (let attempt = 0; attempt < 45; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2_000));
        const statusResponse = asDict(await getJson(
          `/api/observations/${encodeURIComponent(noteRevisionId)}/analysis`,
        ));
        const status = asDict(statusResponse.data);
        if (upper(status.status) === "PENDING") continue;
        if (upper(status.status) === "SUCCEEDED") {
          setNoteSyncMessage("Observation analysis is ready.");
          observationApi.refresh();
          return;
        }
        throw new Error(text(status.error_code, "Observation analysis failed."));
      }
      throw new Error("Observation analysis is still running; refresh later to see its durable result.");
    } catch (cause) {
      setNoteSyncError(cause instanceof Error ? cause.message : "Observation analysis failed.");
    } finally {
      setNoteAnalysisBusyId(null);
    }
  }

  const loading = workbenchApi.loading || (journalTab === "notes" && observationApi.loading);
  const error = workbenchApi.error || (journalTab === "notes" ? observationApi.error : null);
  const readyNotes = filteredExternalNotes.filter((item) => {
    const revision = asDict(item.revision);
    const interpretation = asDict(item.interpretation);
    return upper(revision.coverage) === "FULL" && upper(interpretation.status) === "SUCCEEDED";
  }).length;
  const journalTabs = JOURNAL_TABS.map((item) => ({
    ...item,
    suffix: item.id === "notes" && readyNotes > 0
      ? <span className="horizontal-tab-count">{readyNotes} ready</span>
      : item.id === "reviews" && openReviewCount > 0
        ? <span className="horizontal-tab-count">{openReviewCount}</span>
        : undefined,
    attention: (item.id === "notes" && readyNotes > 0) || (item.id === "reviews" && openReviewCount > 0),
  }));

  return <ConsoleShell active="decision-workbench" pageActions={<PageActionMenu ariaLabel="Journal Page Actions" items={[
    { id: "record-decision", label: "Record Decision", description: "Reuse the current Thesis and Trade Plan context", icon: <ClipboardPenLine aria-hidden="true" />, disabled: !selected || loading, onSelect: () => { setSupersedesDecisionId(null); setDecisionSourceNote(null); setDecisionSourceRevisionId(null); setDecisionSourceReview(null); setDecisionReviewPackage(null); setDecisionDraftScenarios([]); setDecisionError(null); setDecisionOpen(true); } },
    { id: "refresh", label: loading ? "Refreshing…" : "Refresh", description: "Reload durable workflow context", icon: <RefreshCw aria-hidden="true" className={loading ? "spin" : undefined} />, disabled: loading, onSelect: workbenchApi.refresh },
  ]} />}>
    <DataBoundary loading={loading} error={error}>
      <div className="decision-workbench">
        <section className="journal-filter-bar" aria-label="Journal Filters">
          <label><span>Period</span><select value={periodFilter} onChange={(event) => setPeriodFilter(event.target.value as PeriodFilter)}><option value="ALL">All History</option><option value="30D">Last 30 Days</option><option value="90D">Last 90 Days</option><option value="YTD">Year to Date</option><option value="CUSTOM">Custom Range</option></select></label>
          {periodFilter === "CUSTOM" ? <><label><span>Start Date</span><input type="date" value={customPeriodStart} onChange={(event) => setCustomPeriodStart(event.target.value)} /></label><label><span>End Date</span><input type="date" value={customPeriodEnd} onChange={(event) => setCustomPeriodEnd(event.target.value)} /></label></> : null}
          <MultiSelectAutosuggest label="Account" placeholder="All Accounts" options={accountAutosuggestOptions} value={accountFilters} onChange={setAccountFilters} />
          <MultiSelectAutosuggest label="Instrument" placeholder="All Instruments" options={instrumentAutosuggestOptions} value={instrumentFilters} onChange={setInstrumentFilters} />
          <label><span>Quality{journalTab === "behavior" ? " · Cycle Browser Only" : ""}</span><select value={qualityFilter} disabled={journalTab === "behavior"} title={journalTab === "behavior" ? "Behavior preserves calculator exclusions instead of filtering by Cycle quality." : undefined} onChange={(event) => setQualityFilter(event.target.value)}><option value="ALL">All Quality</option><option value="COMPLETE">Complete</option><option value="INCOMPLETE">Incomplete</option></select></label>
          <MultiSelectAutosuggest label="Status" placeholder="All Statuses" options={CYCLE_STATUS_OPTIONS} value={cycleStatusFilters} onChange={(values) => setCycleStatusFilters(values as CycleStatusFilter[])} />
          <details className="journal-more-filters"><summary>More Filters{subjectFilters.length + classificationFilters.length ? ` (${subjectFilters.length + classificationFilters.length})` : ""}</summary><div><MultiSelectAutosuggest label="Research Subject" placeholder="All Research Subjects" options={subjectAutosuggestOptions} value={subjectFilters} onChange={updateSubjectFilters} /><MultiSelectAutosuggest label="Classification" placeholder="All Classifications" options={CLASSIFICATION_OPTIONS} value={classificationFilters} onChange={(values) => setClassificationFilters(values as ActivityClassification[])} /></div></details>
          <p className="journal-filter-scope">Period, Account, Instrument, and Classification are applied to the durable Behavior cohort. Quality applies to the Cycle browser; Subject selects research context and Review scope.{!periodWindowValid ? " The custom date range is invalid: Start Date must not be after End Date." : ""}</p>
        </section>

        {captureContextError ? <div className="inline-error" role="alert">{captureContextError}</div> : null}

        {selected ? <section className="decision-subject-hero journal-subject-context">
            <div><span>{upper(subject.subject_type, "RESEARCH SUBJECT")}</span><h2>{text(subject.title, "Unnamed Research Subject")}</h2><p>{text(subject.summary, "No stable research scope recorded.")}</p></div>
            <div className="decision-subject-meta"><Badge value={upper(subject.status)} /><strong>{shortId(subject.primary_instrument_id)}</strong><small className="mono">{subjectId}</small></div>
          </section> : null}

          <HorizontalTabs items={journalTabs} value={journalTab} onChange={selectJournalTab} ariaLabel="Journal Sections" idPrefix="journal-tab" panelIdPrefix="journal-panel" />

          <section id="journal-panel-overview" role="tabpanel" aria-labelledby="journal-tab-overview" hidden={journalTab !== "overview"} className="journal-panel-stack">
          <CurrentViewCard subjectId={subjectId} currentView={currentView} subject={subject} loading={currentViewApi.loading} error={currentViewApi.error} />
          <Card title="Data Confidence" action={<Badge value={partialFailures.length || transactionWindowLimited || tradeCycleProjectionIncomplete ? "PARTIAL" : "AVAILABLE"} />}>
            <div className="journal-confidence-strip">
              <span>Transactions<strong>{transactionsUnavailable ? "—" : allTransactions.length}</strong><small>{transactionWindowLimited ? "Bounded read" : "Durable facts"}</small></span>
              <span>Complete Cycles<strong>{allTradeCycles.filter((cycle) => upper(cycle.quality) === "COMPLETE").length} / {allTradeCycles.length}</strong><small>Long-only projection</small></span>
              <span>Unresolved<strong>{allTradeCycles.filter((cycle) => upper(cycle.status) === "UNRESOLVED").length}</strong><small>Excluded from outcomes</small></span>
              <span>Account Returns<strong>{performanceSeries.some((item) => item.twr != null) ? "Available" : "Unavailable"}</strong><small>TWR / XIRR / drawdown</small></span>
            </div>
            {partialFailures.length || transactionWindowLimited || tradeCycleProjectionIncomplete ? <div className="journal-remediation"><strong>Why confidence is partial</strong><ul>{partialFailures.map((failure) => <li key={failure}>{metricLabel(failure)} read failed; retry durable context before acting.</li>)}{transactionWindowLimited ? <li>Transaction history reached the 500-row local read boundary.</li> : null}{tradeCycleProjectionIncomplete ? <li>{incompleteCycleCount} Cycle(s) lack complete prices, fees, or reconstruction coverage.</li> : null}</ul><div className="page-actions"><ActionButton onClick={workbenchApi.refresh}>Retry Durable Reads</ActionButton><QuickLink href="/operations">Open Data Quality Center</QuickLink></div></div> : null}
          </Card>

          <Card title="Results" action={<button type="button" onClick={() => selectJournalTab("cycles")}>Open Trade Cycles</button>}>
            <div className="journal-result-grid">
              <span>Closed Cycles<strong>{closedPnlCycles.length}</strong><small>{wins.length} wins · {losses.length} losses</small></span>
              <span>Realized P/L<strong className={realizedPnl < 0 ? "negative" : "positive"}>{formatMoney(realizedPnl)}</strong><small>Known Cycle P/L · fee coverage may be incomplete</small></span>
              <span>Win Rate<strong>{winRate == null ? "—" : `${(winRate * 100).toFixed(1)}%`}</strong><small>{wins.length} / {closedPnlCycles.length}</small></span>
              <span>Payoff Ratio<strong>{payoffRatio == null ? "—" : payoffRatio.toFixed(2)}</strong><small>Average win / average loss</small></span>
              <span>Profit Factor<strong>{profitFactor == null ? "—" : profitFactor.toFixed(2)}</strong><small>Gross wins / gross losses</small></span>
              <span>Median Hold<strong>{medianHoldingDays == null ? "—" : `${medianHoldingDays.toFixed(1)}d`}</strong><small>Closed eligible Cycles</small></span>
            </div>
          </Card>

          <div className="journal-overview-grid">
            <Card title="Holding Patterns">
              <div className="journal-pattern-list">{holdingBuckets.map((bucket) => <div key={bucket.label}><span>{bucket.label}<small>{bucket.count} cycles</small></span><strong className={bucket.pnl < 0 ? "negative" : "positive"}>{formatMoney(bucket.pnl)}</strong></div>)}</div>
            </Card>
            <Card title="Latest Changes" action={<button type="button" onClick={() => selectJournalTab("notes")}>Open Notes</button>}>
              {filteredExternalNotes.length === 0 ? <Empty>No imported note revision matches the current filters.</Empty> : <div className="journal-change-list">{filteredExternalNotes.slice(0, 3).map((item) => { const identity = asDict(item.identity); const revision = asDict(item.revision); const interpretation = asDict(item.interpretation); const payload = asDict(interpretation.payload); return <button type="button" key={text(identity.note_id)} onClick={() => selectJournalTab("notes")}><span><strong>{text(identity.title, "Untitled Note")}</strong><small>{shortId(identity.primary_instrument_id)} · {formatDate(revision.observed_at)}</small></span><span><Badge value={text(interpretation.change_relation, text(interpretation.status, text(revision.coverage)))} /><small>{text(payload.material_change_summary, text(revision.summary, "Awaiting full-text analysis."))}</small></span></button>; })}</div>}
            </Card>
          </div>

          <Card className="journal-instrument-card" title="Traded Instruments" action={<Badge value={transactionWindowLimited ? "BOUNDED" : `${tradedInstrumentRows.length} INSTRUMENTS`} />}>
            <div className="journal-instrument-toolbar">
              <MultiSelectAutosuggest label="Instrument Filter" placeholder="Search traded Instruments" options={instrumentTableOptions} value={instrumentTableFilters} onChange={setInstrumentTableFilters} closeOnSelect />
              <span><strong>{filteredInstrumentRows.length}</strong> of {tradedInstrumentRows.length} Instruments · <strong>{visibleInstrumentFillCount}</strong> fills</span>
            </div>
            {visibleInstrumentRows.length === 0 ? <Empty>No traded Instrument matches the current filters.</Empty> : <>
              <div className="table-wrap journal-instrument-table"><table><thead><tr>
                <SortableTableHeader label="Instrument" column="instrument" activeColumn={instrumentTableSort.key} direction={instrumentTableSort.direction} onSort={changeInstrumentTableSort} />
                <SortableTableHeader label="Fills" column="fills" activeColumn={instrumentTableSort.key} direction={instrumentTableSort.direction} onSort={changeInstrumentTableSort} />
                <SortableTableHeader label="Bought" column="bought" activeColumn={instrumentTableSort.key} direction={instrumentTableSort.direction} onSort={changeInstrumentTableSort} />
                <SortableTableHeader label="Sold" column="sold" activeColumn={instrumentTableSort.key} direction={instrumentTableSort.direction} onSort={changeInstrumentTableSort} />
                <SortableTableHeader label="Accounts" column="accounts" activeColumn={instrumentTableSort.key} direction={instrumentTableSort.direction} onSort={changeInstrumentTableSort} />
                <SortableTableHeader label="Closed Cycles" column="closedCycles" activeColumn={instrumentTableSort.key} direction={instrumentTableSort.direction} onSort={changeInstrumentTableSort} />
                <SortableTableHeader label="Known P/L" column="knownPnl" activeColumn={instrumentTableSort.key} direction={instrumentTableSort.direction} onSort={changeInstrumentTableSort} />
                <SortableTableHeader label="Last Trade" column="lastTradeAt" activeColumn={instrumentTableSort.key} direction={instrumentTableSort.direction} onSort={changeInstrumentTableSort} />
              </tr></thead><tbody>{visibleInstrumentRows.map((row) => <tr key={row.instrument}>
                <td><strong>{shortId(row.instrument)}</strong><small className="table-sub mono">{row.instrument}</small></td>
                <td>{row.fills}</td>
                <td>{formatDecimal(row.bought, 4)}</td>
                <td>{formatDecimal(row.sold, 4)}</td>
                <td>{row.accounts}</td>
                <td>{row.closedCycles}</td>
                <td><strong className={row.knownPnl < 0 ? "negative" : row.knownPnl > 0 ? "positive" : ""}>{row.pnlCycles ? formatMoney(row.knownPnl) : "—"}</strong><small className="table-sub">P/L available for {row.pnlCycles} / {row.closedCycles} closed Cycles</small></td>
                <td>{formatDate(row.lastTradeAt)}<small className="table-sub">First {formatDate(row.firstTradeAt)}</small></td>
              </tr>)}</tbody></table></div>
              <Paginator step={INSTRUMENT_TABLE_PAGE_SIZE} offset={instrumentTableOffset} hasMore={instrumentTableOffset + INSTRUMENT_TABLE_PAGE_SIZE < filteredInstrumentRows.length} onOffsetChange={setInstrumentTableOffset} summary={<small>{instrumentTableOffset + 1}–{Math.min(instrumentTableOffset + INSTRUMENT_TABLE_PAGE_SIZE, filteredInstrumentRows.length)} of {filteredInstrumentRows.length}</small>} />
            </>}
          </Card>

          <Card className="decision-next-card" title="Needs Review" action={<Badge value={partialFailures.length ? "INCOMPLETE" : compactNextSteps.length ? `${compactNextSteps.length} GROUPS` : "READY"} />}><StepList items={compactNextSteps.slice(0, 4)} busy={reviewBusy} onTransition={(item, status) => { void transitionReviewItem(item, status); }} /><ErrorNote>{reviewError}</ErrorNote>{decisionMessage ? <div className="inline-success">{decisionMessage}</div> : null}</Card>

          {selected ? <div className="decision-stage-grid journal-research-context">
            <Card kicker="1 · DECIDE" title="Judgment & Plan" action={<div className="workflow-card-actions"><ActionButton onClick={() => { setSupersedesDecisionId(null); setDecisionSourceNote(null); setDecisionSourceRevisionId(null); setDecisionSourceReview(null); setDecisionReviewPackage(null); setDecisionDraftScenarios([]); setDecisionError(null); setDecisionOpen(true); }}>Record Decision</ActionButton><QuickLink href={`/research#subject-${subjectId}`}>Open Research</QuickLink></div>}>
              <div className="decision-stage-lead"><Badge value={researchUnavailable || timelineUnavailable ? "INCOMPLETE" : latestDecision ? "DECISION" : primaryThesis ? upper(primaryThesis.status) : "MISSING"} /><strong>{researchUnavailable ? "Research state read unavailable" : timelineUnavailable ? "Decision Timeline unavailable" : text(latestDecision?.title, text(primaryThesis?.title, "No live Thesis"))}</strong><ScenarioDigest value={researchUnavailable || timelineUnavailable ? "Retry the missing durable read before interpreting the decision chain." : text(latestDecision?.summary, text(primaryRevision?.statement, "Create a falsifiable judgment before defining execution intent."))} /></div>
              <div className="workflow-support-line"><span>Trade Plan</span><Badge value={researchUnavailable ? "UNAVAILABLE" : plan ? upper(plan.status) : "MISSING"} /><strong>{plan ? `${shortId(plan.instrument_id)} · v${number(plan.version) || "—"}` : "No Current Plan"}</strong></div>
              <div className="decision-metrics"><span>Recent Decisions<strong>{decisionItems.length}</strong></span><span>Plan Conditions<strong>{listOf<Dict>(plan, "conditions").length}</strong></span><span>Pending Reviews<strong>{pendingCandidates.length + openQuestions.length}</strong></span></div>
            </Card>

            <Card kicker="2 · OBSERVE" title="Evidence & Triggers" action={<QuickLink href="/monitors">Open Monitors</QuickLink>}>
              <div className="decision-stage-lead"><Badge value={monitorsUnavailable ? "UNAVAILABLE" : activeMonitors.length ? "ACTIVE" : "UNCOVERED"} /><strong>{monitorsUnavailable ? "Monitor dashboard read unavailable" : `${linkedMonitors.length} linked · ${activeMonitors.length} active`}</strong><p>{monitorsUnavailable ? "Coverage is unknown; absence must not be interpreted as no Monitor." : latestMonitorRun ? `Latest run ${upper(latestMonitorRun.status)} · ${formatDate(latestMonitorRun.completed_at ?? latestMonitorRun.started_at)}` : "No linked Monitor run has been recorded."}</p></div>
              <div className="workflow-support-line"><span>Next Catalyst</span><Badge value={agendaUnavailable ? "UNAVAILABLE" : overdueCatalysts.length ? "OVERDUE" : "TRACKING"} /><strong>{agendaUnavailable ? "Agenda Unavailable" : upcomingCatalysts[0] ? `${text(upcomingCatalysts[0].title)} · ${formatDate(upcomingCatalysts[0].window_start)}` : "No Upcoming Catalyst"}</strong></div>
              <div className="decision-metrics"><span>Triggered Rules<strong>{triggeredRules.length}</strong></span><span>Upcoming Events<strong>{upcomingCatalysts.length}</strong></span><span>Overdue Outcomes<strong>{overdueCatalysts.length}</strong></span></div>
            </Card>

            <Card kicker="3 · EXECUTE" title="Position & Trade Cycles" action={<QuickLink href="/portfolio">Open Portfolio</QuickLink>}>
              <div className="decision-stage-lead"><Badge value={accountsUnavailable || transactionsUnavailable || tradeCyclesUnavailable ? "INCOMPLETE" : transactionWindowLimited || tradeCycleProjectionIncomplete ? "PARTIAL" : relatedPositions.length ? "HELD" : relatedTransactions.length ? "CLOSED / ACTIVITY" : "NO ACTIVITY"} /><strong>{instrumentId ? shortId(instrumentId) : "No Execution Instrument"}</strong><p>{transactionsUnavailable ? "Durable transaction history is unavailable; do not interpret the empty state as no trading." : latestTransaction ? `${upper(latestTransaction.side, upper(latestTransaction.kind))} · ${formatDate(latestTransaction.occurred_at)} · ${text(latestTransaction.quantity, "—")} @ ${text(latestTransaction.price, "—")}` : "No durable activity is linked to this execution Instrument."}</p></div>
              <div className="workflow-support-line"><span>Latest Trade Cycle</span><Badge value={tradeCyclesUnavailable ? "UNAVAILABLE" : upper(latestTradeCycle?.status, "NOT READY")} /><strong>{tradeCyclesUnavailable ? "Cycle projection is unavailable" : latestTradeCycle && latestCyclePnl ? `${formatDate(latestTradeCycle.opened_at)} → ${latestTradeCycle.closed_at ? formatDate(latestTradeCycle.closed_at) : "Open"} · ${latestCyclePnl.label} ${latestCyclePnl.value}${latestCyclePnl.detail ? ` · ${latestCyclePnl.detail}` : ""}${tradeCycleProjectionIncomplete ? " · Coverage Incomplete" : ""}` : relatedTransactions.length ? "No resolvable long-only Cycle; inspect durable activity coverage" : "A Fill is required before a Cycle can open"}</strong></div>
              <div className="decision-metrics"><span>Held Quantity<strong>{heldQuantity || "—"}</strong></span><span>Trade Cycles<strong>{subjectTradeCycles.length}</strong></span><span>Visible Activity<strong>{relatedTransactions.length}</strong></span></div>
            </Card>

            <Card kicker="4 · REVIEW" title="Performance & Behavior" action={<div className="workflow-card-actions"><QuickLink href="/portfolio">Performance</QuickLink><ActionButton onClick={() => selectJournalTab("reviews")}>Review Details</ActionButton></div>}>
              <div className="decision-stage-lead"><Badge value={retroUnavailable ? "UNAVAILABLE" : latestRetro ? retroReviewStatus : "NO RUN"} /><strong>{retroUnavailable ? "Period review history unavailable" : latestRetro ? `${text(latestRetro.period_start).slice(0, 10)} → ${text(latestRetro.period_end).slice(0, 10)}` : "No related review"}</strong><p>{retroUnavailable ? "Review status and findings are unknown until the durable read recovers." : latestRetro ? `${listOf<Dict>(latestRetro, "findings").length} deterministic finding(s) · generated ${formatDate(latestRetro.generated_at)}` : "Performance remains in Portfolio; behavior review begins after durable transactions and a pre-period plan snapshot exist."}</p></div>
              <div className="workflow-support-line"><span>Judgment Calibration</span><Badge value={scorecardsUnavailable ? "UNAVAILABLE" : scorecardGaps.length ? "GAPS" : latestScorecard ? "AVAILABLE" : "NO RUN"} /><strong>{latestScorecard ? `${scorecardGaps.length} gap(s) across ${listOf<Dict>(latestScorecard, "dimensions").length} dimensions` : "No Calibration Evidence Yet"}</strong></div>
              <div className="decision-metrics"><span>Review Findings<strong>{listOf<Dict>(latestRetro, "findings").length}</strong></span><span>Follow-Up Actions<strong>{listOf<string>(retroReview, "action_items").length}</strong></span><span>Calibration Gaps<strong>{scorecardGaps.length}</strong></span></div>
            </Card>
          </div> : null}
          </section>

          <section id="journal-panel-timeline" role="tabpanel" aria-labelledby="journal-tab-timeline" hidden={journalTab !== "timeline"} className="journal-panel-stack">
            <Card kicker="DECISIONS + ACTIVITY" title="Timeline" action={<Badge value={`${journalTimelineRows.length} ITEMS`} />}>
              <p className="card-note">Research Decisions, Trading Partner order intents/results, and durable Broker activities share one chronological view. Missing Provider history or an absent exact link remains a coverage gap; this page never refreshes a Broker.</p>
              {journalTimelineRows.length === 0 ? <Empty>No Decision or durable activity is available for this Research Subject.</Empty> : <><div className="journal-timeline-list">{visibleTimelineRows.map((item) => <article key={`${item.kind}-${item.key}`}><Badge value={item.kind} /><div><header><strong>{item.title}</strong><time>{formatDate(item.occurredAt)}</time></header><p>{item.detail}</p></div></article>)}</div><Paginator step={TIMELINE_PAGE_SIZE} offset={timelineOffset} hasMore={timelineOffset + TIMELINE_PAGE_SIZE < journalTimelineRows.length} onOffsetChange={setTimelineOffset} summary={<small>{timelineOffset + 1}–{Math.min(timelineOffset + TIMELINE_PAGE_SIZE, journalTimelineRows.length)} of {journalTimelineRows.length}</small>} /></>}
            </Card>
          </section>

          <section id="journal-panel-cycles" role="tabpanel" aria-labelledby="journal-tab-cycles" hidden={journalTab !== "cycles"} className="journal-panel-stack">
            <Card kicker="DETERMINISTIC · LONG-ONLY" title="Trade Cycles" action={<div className="cycle-header-actions"><div className={`cycle-quality-indicator ${overallCycleQuality === "COMPLETE" ? "complete" : "incomplete"}`}><span>Data Quality</span><strong>{overallCycleQuality === "COMPLETE" ? "Complete" : overallCycleQuality === "INCOMPLETE" ? "Incomplete" : "Unknown"}</strong><small>{overallCycleQuality === "COMPLETE" ? `${allTradeCycles.length} Cycles fully reconstructable` : `${incompleteCycleCount} incomplete · including ${unresolvedCycleCount} unresolved`}</small></div>{tradeCycleOverrides.length > 0 ? <Badge value={`${tradeCycleOverrides.length} OVERRIDES`} /> : null}<QuickLink href="/portfolio#activity">Open Portfolio</QuickLink></div>}>
              <p className="card-note">Cycles are rebuilt from durable transactions by account, Instrument, and native currency. Append-only split/merge/relink revisions change only the effective projection; the original algorithm Cycles remain retained.</p>
              <div className="cycle-sort-toolbar"><span>Latest Activity uses the newest exact loaded activity in each Cycle; when that activity is outside the current transaction window, the Cycle close/open time is the explicit fallback.</span><label><span>Sort Cycles</span><select value={cycleSortMode} onChange={(event) => setCycleSortMode(event.target.value as CycleSortMode)}><option value="LATEST_DESC">Latest Activity · Newest First</option><option value="LATEST_ASC">Latest Activity · Oldest First</option><option value="OPENED_DESC">Opened · Newest First</option><option value="OPENED_ASC">Opened · Oldest First</option><option value="INSTRUMENT_ASC">Instrument · A to Z</option><option value="INSTRUMENT_DESC">Instrument · Z to A</option></select></label></div>
              <div className="cycle-status-guide" aria-label="Trade Cycle Status and Quality Guide"><span><Badge value="OPEN" tone="good" /><strong>{cycleStatusCounts.OPEN}</strong><small>Position quantity remains above zero.</small></span><span><Badge value="CLOSED" tone="neutral" /><strong>{cycleStatusCounts.CLOSED}</strong><small>Matched activity returned quantity to zero.</small></span><span><Badge value="UNRESOLVED" tone="bad" /><strong>{cycleStatusCounts.UNRESOLVED}</strong><small>Available history cannot reconstruct a valid long-only Cycle.</small></span><span><Badge value="INCOMPLETE" tone="warn" /><strong>{cycleIncompleteCount}</strong><small>Cycle exists, but fees, prices, or coverage are missing.</small></span></div>
              {tradeCycles.length === 0 ? <Empty>No resolvable Trade Cycle matches the current filters.</Empty> : <>
                <div className={`journal-cycle-browser rows-${cyclePageSize}`}>
                  <div className="journal-cycle-browser-list" role="list" aria-label="Trade Cycles">
                    {visibleTradeCycles.map((cycle) => { const pnl = cyclePnlPresentation(cycle); const latestActivityTime = cycleLatestActivityTime(cycle); return <button type="button" role="listitem" className={text(selectedCycle?.cycle_id) === text(cycle.cycle_id) ? "selected" : ""} key={text(cycle.cycle_id)} onClick={() => setSelectedCycleId(text(cycle.cycle_id))}>
                      <span><strong>{shortId(cycle.instrument_id)}</strong><small>{formatDate(cycle.opened_at)} → {cycle.closed_at ? formatDate(cycle.closed_at) : "Open"}</small><small>Latest Activity {latestActivityTime > 0 ? formatDate(new Date(latestActivityTime).toISOString()) : "Unavailable"}</small></span>
                      <span><Badge value={text(cycle.status, "UNKNOWN")} tone={cycleStatusTone(cycle.status)} /><strong className={pnl.tone}>{pnl.label} {pnl.value}</strong>{pnl.detail ? <small>{pnl.detail}</small> : null}</span>
                    </button>; })}
                    <Paginator step={cyclePageSize} offset={cycleOffset} hasMore={cycleOffset + cyclePageSize < tradeCycles.length} onOffsetChange={(value) => { setCycleOffset(value); setSelectedCycleId(""); }} summary={<small>{cycleOffset + 1}–{Math.min(cycleOffset + cyclePageSize, tradeCycles.length)} of {tradeCycles.length}</small>} />
                  </div>
                  {selectedCycle ? <article className="journal-cycle-detail">
                    <header><div><span>Trade Cycle</span><h3>{shortId(selectedCycle.instrument_id)}</h3><p>{formatDate(selectedCycle.opened_at)} → {selectedCycle.closed_at ? formatDate(selectedCycle.closed_at) : "Open"}</p></div><div className="page-actions"><Badge value={text(selectedCycle.classification, "UNCLASSIFIED")} tone={cycleClassificationTone(selectedCycle.classification)} /><Badge value={text(selectedCycle.status, "UNKNOWN")} tone={cycleStatusTone(selectedCycle.status)} /><Badge value={text(selectedCycle.quality, "UNKNOWN")} tone={cycleQualityTone(selectedCycle.quality)} /></div></header>
                    <div className="journal-result-grid compact"><span>{selectedCyclePnl?.label ?? "P/L"}<strong className={selectedCyclePnl?.tone}>{selectedCyclePnl?.value ?? "Unavailable"}</strong>{selectedCyclePnl?.detail ? <small>{selectedCyclePnl.detail}</small> : null}</span><span>Maximum Deployed<strong>{selectedCycle.maximum_deployed_capital == null ? "Unavailable" : formatMoney(number(selectedCycle.maximum_deployed_capital), text(selectedCycle.currency, "USD"))}</strong></span><span>Ending Quantity<strong>{formatDecimal(selectedCycle.ending_quantity)}</strong></span><span>Adds / Reductions<strong>{text(selectedCycle.add_count, "0")} / {text(selectedCycle.reduce_count, "0")}</strong></span></div>
                    <section><h4>Activity Path</h4>{selectedCycleTransactions.length === 0 ? <Empty>Exact activity details are unavailable in the current durable window.</Empty> : <div className="journal-timeline-list">{selectedCycleTransactions.map((transaction) => <article key={text(transaction.provider_transaction_id)}><Badge value={upper(transaction.side, upper(transaction.kind))} /><div><header><strong>{text(transaction.quantity)} @ {text(transaction.price)} {text(transaction.currency, "")}</strong><time>{formatDate(transaction.occurred_at)}</time></header><p>{shortId(transaction.account_ref)} · {text(transaction.provider)}</p></div></article>)}</div>}</section>
                    <section><h4>Decision & Plan</h4><p className="card-note">{selected ? latestDecision ? `${text(latestDecision.title)} · ${planLinkReady ? `Trade Plan v${planVersion}` : "No exact Trade Plan link"}` : "No exact pre-fill Decision is linked in the selected Research Subject." : "Select a Research Subject to inspect exact Decision and Trade Plan context. Instrument and timing alone are not treated as proof of a link."}</p></section>
                    {listOf<string>(selectedCycle, "warning_codes").length > 0 ? <section><h4>Data Warnings</h4><div className="retro-code-list">{listOf<string>(selectedCycle, "warning_codes").map((warning) => <code key={warning}>{warning}</code>)}</div></section> : null}
                  </article> : null}
                </div>
              </>}
              <CycleAdjustmentEditor cycles={tradeCycles} transactions={allTransactions} overrideRevisions={tradeCycleOverrides} onApplied={workbenchApi.refresh} />
            </Card>
          </section>

          <section id="journal-panel-behavior" role="tabpanel" aria-labelledby="journal-tab-behavior" hidden={journalTab !== "behavior"} className="journal-panel-stack">
            <Card title="Behavior" action={<Badge value={text(behaviorData.algorithm_version, "UNAVAILABLE")} />}>
              <div className="journal-scope-notice"><strong>Cohort scope</strong><span>{subjectId ? shortId(subjectId) : "All Subjects"} · {accountFilters.length ? `${accountFilters.length} selected Account(s)` : "All Accounts"} · {instrumentFilters.length ? instrumentFilters.map(shortId).join(", ") : instrumentId ? shortId(instrumentId) : "All Instruments"} · {classificationFilters.length ? classificationFilters.join(", ") : "All Classifications"} · {asDict(behaviorData.cohort).start ? formatDate(asDict(behaviorData.cohort).start) : selectedPeriodStart ? formatDate(selectedPeriodStart) : "All Dates"} → {asDict(behaviorData.cohort).end ? formatDate(asDict(behaviorData.cohort).end) : selectedPeriodEnd ? formatDate(selectedPeriodEnd) : "Now"}</span></div>
              {!periodWindowValid ? <ErrorNote>Start Date must not be after End Date. Behavior remains unavailable until the range is corrected.</ErrorNote> : null}
              <p className="card-note">Each metric appears once. Ratios are recomputed from the returned numerator and denominator before display; inconsistent payloads fail closed. Exclusions and unsupported facts remain visible instead of becoming zero.</p>
              {periodWindowValid ? <BehaviorPanel value={behaviorData} /> : <Empty>Correct the custom date range to calculate Behavior.</Empty>}
            </Card>
          </section>

          <section id="journal-panel-notes" role="tabpanel" aria-labelledby="journal-tab-notes" hidden={journalTab !== "notes"} className="journal-panel-stack">
            <ObservationInbox
              items={filteredExternalNotes}
              sources={observationSources}
              activeSubjects={activeSubjects}
              selectedInstrumentIds={activeInstrumentFilters}
              busy={noteSyncBusy}
              syncMessage={noteSyncMessage}
              syncError={noteSyncError}
              onRefresh={() => { void refreshObservationSources(); }}
              onSelectSubject={selectSubjectContext}
              onReviewDecision={reviewNoteAsDecision}
              onDeferReview={deferNoteReview}
              analysisBusyId={noteAnalysisBusyId}
              reviewBusyId={noteReviewBusyId}
              onAnalyzeRevision={(revisionId) => { void analyzeObservation(revisionId); }}
              positionContext={(noteInstrumentId) => { if (!noteInstrumentId) return <div><strong>Unverified</strong><small>Canonical Instrument unresolved</small></div>; const matches = allPositions.filter((position) => text(position.instrument_id, "") === noteInstrumentId); const quantity = matches.reduce((total, position) => total + number(position.quantity), 0); return <div><strong>{matches.length ? quantity : "No Position"}</strong><small>{matches.length ? `${matches.length} account position${matches.length === 1 ? "" : "s"}` : "No durable holding in the current snapshot"}</small></div>; }}
              cyclesContext={(noteInstrumentId) => { if (!noteInstrumentId) return <div><strong>Unverified</strong><small>Canonical Instrument unresolved</small></div>; const matches = allTradeCycles.filter((cycle) => text(cycle.instrument_id, "") === noteInstrumentId); const latest = [...matches].sort((left, right) => cycleReviewTime(right) - cycleReviewTime(left))[0]; const pnl = latest ? cyclePnlPresentation(latest) : null; return <div><strong>{matches.length} Cycle{matches.length === 1 ? "" : "s"}</strong><small>{latest && pnl ? `${upper(latest.status)} · ${pnl.label} ${pnl.value}${pnl.detail ? ` · ${pnl.detail}` : ""}` : "No durable Trade Cycle"}</small></div>; }}
            />
          </section>

          <section id="journal-panel-reviews" role="tabpanel" aria-labelledby="journal-tab-reviews" hidden={journalTab !== "reviews"} className="journal-panel-stack">
            <Card title="Reviews" action={<div className="workflow-card-actions"><ActionButton busy={behaviorReviewBusy} onClick={() => { void prepareWeeklyReview(); }}>Preview Weekly Review</ActionButton>{subjectId ? <QuickLink href={`/scorecards?subject_id=${encodeURIComponent(subjectId)}`}>Calibration</QuickLink> : null}</div>}>
              <div className="decision-metrics"><span>Active Queue<strong>{openReviewCount}</strong><small>Open + acknowledged</small></span><span>Latest Run Findings<strong>{listOf<Dict>(latestRetro, "findings").length}</strong><small>Not the queue total</small></span><span>Calibration Gaps<strong>{scorecardGaps.length}</strong></span></div>
              <StepList items={visibleReviewSteps} busy={reviewBusy} onTransition={(item, status) => { void transitionReviewItem(item, status); }} />
              {nextSteps.length > reviewPageSize ? <Paginator step={reviewPageSize} offset={reviewOffset} hasMore={reviewOffset + reviewPageSize < nextSteps.length} onOffsetChange={setReviewOffset} summary={<small>{reviewOffset + 1}–{Math.min(reviewOffset + reviewPageSize, nextSteps.length)} of {nextSteps.length} active items</small>} /> : null}
              <ErrorNote>{behaviorReviewError}</ErrorNote>
              {behaviorReviewRuns.length > 0 ? <div className="journal-cycle-list">{behaviorReviewRuns.slice(0, 8).map((run) => <article key={text(run.run_id)}><header><div><strong>{text(asDict(run.cohort).period_kind)} · {text(asDict(run.cohort).period_start).slice(0, 10)}</strong><small>{formatDate(run.generated_at)}</small></div><Badge value={text(run.status)} /></header><div className="page-actions">{listOf<Dict>(run, "action_observations").map((item) => <Badge key={text(item.observation_id)} value={text(item.status)} />)}</div><small className="table-sub">NEW, PERSISTENT, RESOLVED, and RECURRED are derived only from complete durable action-source reads.</small></article>)}</div> : <Empty>No period Behavior Review has been recorded yet.</Empty>}
            </Card>
            <Card title="Period Review History" action={<Badge value={`${relatedRetro.length} RUNS`} />}>
              <RetroReviewList runs={relatedRetro} onUpdated={workbenchApi.refresh} />
            </Card>
          </section>
        {partialFailures.length ? <div id="decision-context-status" className="inline-error">Incomplete durable context: {partialFailures.join(", ")}. Available sections remain readable; retry before acting on the missing context.</div> : null}
        <TextInputDialog
          open={reviewInput !== null}
          title={reviewInput?.kind === "resolution" ? "Resolve Review Item" : "Update Review Due Date"}
          description={reviewInput ? `${reviewInput.kind === "resolution" ? "This closes" : "Update the optional due date for"} “${text(reviewInput.item.title, "this review item")}”.` : undefined}
          label={reviewInput?.kind === "resolution" ? "Resolution Note" : "Due Date"}
          value={reviewInputValue}
          onChange={setReviewInputValue}
          onSubmit={submitReviewInput}
          onCancel={() => setReviewInput(null)}
          required={reviewInput?.kind === "resolution"}
          inputType={reviewInput?.kind === "due" ? "date" : "text"}
          multiline={reviewInput?.kind === "resolution"}
          helperText={reviewInput?.kind === "resolution" ? "Record the durable fact or completed action that closes this item." : "Optional. Leave blank to keep the current due date."}
          error={reviewInputError}
          confirmLabel={reviewInput?.kind === "resolution" ? "Resolve Item" : "Save Due Date"}
          tone={reviewInput?.kind === "resolution" ? "warning" : "default"}
        />
        <ConfirmationDialog
          open={reviewAcknowledgement !== null}
          title="Acknowledge Review Item"
          description={reviewAcknowledgement ? `Keep “${text(reviewAcknowledgement.title, "this review item")}" active while recording that you have seen it.` : undefined}
          confirmLabel="Acknowledge Item"
          busy={reviewAcknowledgement ? reviewBusy === text(reviewAcknowledgement.review_item_id, "") : false}
          onConfirm={() => { if (reviewAcknowledgement) { const item = reviewAcknowledgement; setReviewAcknowledgement(null); void persistReviewItem(item, "ACKNOWLEDGED"); } }}
          onCancel={() => setReviewAcknowledgement(null)}
        >
          <div className="confirmation-facts"><span>Status change<strong>OPEN → ACKNOWLEDGED</strong></span><span>Queue behavior<strong>Remains active</strong></span><p>This is a durable, versioned transition. It does not resolve the underlying source.</p></div>
        </ConfirmationDialog>
        <ConfirmationDialog
          open={weeklyReviewPreview !== null}
          title="Create Weekly Review"
          description="This confirmed workflow performs three durable writes for the exact canonical windows shown below."
          confirmLabel="Run 3-Step Review"
          busy={behaviorReviewBusy}
          onConfirm={() => { void createWeeklyReview(); }}
          onCancel={() => { if (!behaviorReviewBusy) setWeeklyReviewPreview(null); }}
        >
          {weeklyReviewPreview ? <div className="confirmation-facts"><span>1 · Generate immutable Trade Retro<strong>{weeklyReviewPreview.start.slice(0, 10)} → {weeklyReviewPreview.end.slice(0, 10)}</strong></span><span>2 · Append Behavior Review<strong>{subjectId ? shortId(subjectId) : "Global cohort"}</strong><small>{tradeCycles.length} loaded Cycle refs · {decisionItems.length} Decision refs · {reviewItems.length} Review refs</small></span><span>3 · Prepare next period<strong>{weeklyReviewPreview.nextStart.slice(0, 10)} → {weeklyReviewPreview.nextEnd.slice(0, 10)}</strong></span><p>{retroUnavailable || reviewItemsUnavailable ? "Source reads are incomplete; the Behavior Review will preserve that limitation." : "Retro and Review Queue reads are currently available."}</p></div> : null}
          <ErrorNote>{behaviorReviewError}</ErrorNote>
        </ConfirmationDialog>
        <ConfirmationDialog
          open={observationDefer !== null}
          title="Defer View Review"
          description="Keep this exact Observation revision pending and set when it should return to attention. This records no investment Decision."
          confirmLabel="Defer Review"
          busy={observationDeferBusy}
          onConfirm={() => { void confirmObservationDefer(); }}
          onCancel={() => { if (!observationDeferBusy) setObservationDefer(null); }}
        >
          <div className="journal-capture-form">
            <div className="confirmation-facts journal-capture-wide"><span>View Change<strong>{observationDefer?.title ?? "—"}</strong></span></div>
            <FormField label="Review Date" required className="journal-capture-wide"><input type="date" required value={observationDefer?.dueDate ?? ""} onChange={(event) => setObservationDefer((current) => current ? { ...current, dueDate: event.target.value } : current)} /></FormField>
          </div>
        </ConfirmationDialog>
        <ConfirmationDialog
          open={decisionOpen}
          title={decisionSourceRevisionId ? "Review View Change" : "Record Decision"}
          description={supersedesDecisionId ? `Review and supersede ${shortId(supersedesDecisionId)} with a new durable Decision. This does not submit or authorize an order.` : decisionSourceRevisionId ? "Compare the imported view with the current confirmed baseline, edit the conclusion, then record one durable Decision. No position, Monitor, or order changes automatically." : "Save the current strategy decision in the existing durable Decision Record. This does not submit or authorize an order."}
          confirmLabel={decisionSourceRevisionId ? "Confirm & Record Decision" : "Save Decision"}
          busy={decisionBusy}
          onConfirm={() => { void saveDecision(); }}
          onCancel={() => { if (!decisionBusy) { setDecisionOpen(false); setSupersedesDecisionId(null); setDecisionSourceNote(null); setDecisionSourceRevisionId(null); setDecisionSourceReview(null); setDecisionReviewPackage(null); setDecisionDraftScenarios([]); } }}
        >
          <div className="journal-capture-form">
            <div className="confirmation-facts journal-capture-wide"><span>Research Subject<strong>{text(subject.title, "No Subject")}</strong><small>{upper(subject.status, "UNKNOWN")}</small></span><span>Trade Plan<strong>{planLinkReady ? `${shortId(plan?.instrument_id)} · v${planVersion}` : "No exact Plan linked"}</strong></span><span>Source Note Revision<strong>{decisionSourceNote || "None"}</strong></span><span>Current Position<strong>{relatedPositions.length ? `${heldQuantity} across ${relatedPositions.length} account snapshot row(s)` : "No durable position row in current snapshot"}</strong></span></div>
            {decisionSourceRevisionId ? <Disclosure className="journal-capture-wide" variant="code" title="Exact Durable Provenance"><pre>{JSON.stringify({ subject_id: subjectId, trade_plan_id: planLinkReady ? planId : null, trade_plan_version: planLinkReady ? planVersion : null, note_revision_id: decisionSourceRevisionId }, null, 2)}</pre></Disclosure> : null}
            {decisionReviewPackage ? <div className="journal-capture-wide notes-review-baseline"><span className="card-kicker">CONFIRMED BASELINE COMPARISON</span><div className="confirmation-facts"><span>What Changed<strong>{text(decisionReviewPackage.material_change_summary, "No change summary available")}</strong></span><span>Current Thesis<strong>{text(asDict(decisionReviewPackage.thesis).statement, "No live Thesis")}</strong></span><span>Prior Decision<strong>{text(asDict(decisionReviewPackage.latest_decision).title, "No prior Decision")}</strong></span><span>Deep Review<strong>{text(asDict(decisionReviewPackage.deep_review).status, "Not Configured")}</strong><small>{text(asDict(decisionReviewPackage.deep_review).model, "Flash draft remains available")}</small></span><span>Deterministic Checks<strong>{listOf<string>(decisionReviewPackage, "deterministic_flags").map((item) => item.replaceAll("_", " ")).join(" · ") || "No structural conflict detected"}</strong></span></div><small>Flash and Max text are drafts. Thesis, Plan, Position, Monitor, and coverage checks above come from durable local records.</small></div> : null}
            <FormField label="Action" required><select required value={decisionAction} onChange={(event) => setDecisionAction(event.target.value as DecisionAction)}>{DECISION_ACTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></FormField>
            <FormField label="Current Scenario" required><select required value={decisionScenario} onChange={(event) => setDecisionScenario(event.target.value as DecisionScenario)}>{DECISION_SCENARIOS.map((scenario) => <option key={scenario} value={scenario}>{scenario}</option>)}</select></FormField>
            <FormField label="Reason" required className="journal-capture-wide"><textarea required value={decisionReason} onChange={(event) => { setDecisionReason(event.target.value); setDecisionError(null); }} placeholder="What fact, structure, or risk constraint supports this decision?" /></FormField>
            {decisionDraftScenarios.length ? <div className="notes-scenario-grid journal-capture-wide" aria-label="Imported Note Scenario Draft">{DECISION_SCENARIOS.map((scenario) => { const draft = decisionDraftScenarios.find((item) => upper(item.scenario) === scenario); return <article className="notes-scenario-card" key={scenario}><header><strong>{scenario}</strong><Badge value={upper(draft?.action, "REVIEW")} /></header><p>{text(draft?.condition, "No imported condition.")}</p></article>; })}</div> : null}
            <FormField label="Review Date" className="journal-capture-wide"><input type="date" value={decisionReviewDate} onChange={(event) => setDecisionReviewDate(event.target.value)} /></FormField>
            <p className="card-note journal-capture-wide">Strategy is recorded as strategy_v1{planLinkReady ? ` and linked to Trade Plan v${planVersion}` : ""}. The selected scenario records this action; the other scenarios remain REVIEW under the current confirmed Plan.{decisionSourceNote ? " This draft came from an exact imported Note revision; review and edit it before saving." : ""}</p>
            <ErrorNote role="alert">{decisionError}</ErrorNote>
          </div>
        </ConfirmationDialog>
      </div>
    </DataBoundary>
  </ConsoleShell>;
}
