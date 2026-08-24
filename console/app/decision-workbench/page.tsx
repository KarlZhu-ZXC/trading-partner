"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ClipboardPenLine, RefreshCw } from "lucide-react";
import { ErrorNote, ActionButton, Badge, Card, ConfirmationDialog, DataBoundary, Empty, FormField, HorizontalTabs, PageActionMenu, Paginator, TextInputDialog, formatDate, shortId } from "../components/ui";
import { ConsoleShell } from "../components/console-shell";
import { envelopeData, listOf, postApi, useApi } from "../lib/api";
import { endOfDayIsoOrNull } from "../lib/review-due-date.mjs";
import { useAgentPageContext } from "../lib/agent-page-context";

type Dict = Record<string, unknown>;
type SubjectAggregate = { subject?: Dict; state?: Dict };
type NextStep = { key: string; severity: string; title: string; detail: string; href: string; reviewItem?: Dict };
type ReviewInput = { kind: "resolution" | "due"; item: Dict; status: "ACKNOWLEDGED" | "RESOLVED" };
type DecisionAction = "watch" | "no_action" | "initiate_intent" | "add_intent" | "hold" | "reduce_intent" | "exit_intent" | "avoid" | "research_more";
type DecisionScenario = "UPSIDE" | "SIDEWAYS" | "PULLBACK" | "INVALIDATION";
type JournalTab = "overview" | "timeline" | "cycles" | "behavior" | "reviews";
type ActivityStatus = "LINKED_DECISION_PLAN" | "UNPLANNED" | "CASH_MANAGEMENT" | "TRANSFER_OR_CORPORATE_ACTION" | "PROVIDER_CORRECTION";
type ActivityClassification = "ACTIVE_TRADE" | "LONG_TERM_INVESTMENT" | "HEDGE" | "CASH_MANAGEMENT" | "TRANSFER_OR_ADMIN" | "UNCLASSIFIED";

const JOURNAL_TABS: Array<{ id: JournalTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "timeline", label: "Timeline" },
  { id: "cycles", label: "Trade Cycles" },
  { id: "behavior", label: "Behavior" },
  { id: "reviews", label: "Reviews" },
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

const BEHAVIOR_DETAILS = [
  "win_rate", "avg_win", "avg_loss", "payoff_ratio", "average_holding_duration",
  "median_holding_duration", "turnover", "plan_coverage", "pre_fill_decision_coverage",
  "pre_fill_invalidation_proxy", "invalidation_adherence", "same_day_reentry",
  "entry_attempt_count", "third_attempt_without_new_plan", "add_confirmation_risk_control",
  "planned_holding_period_mismatch", "no_action_review_completion",
];

function behaviorPercent(metric: Dict): string {
  return metric.value == null ? "—" : `${(number(metric.value) * 100).toFixed(1)}%`;
}

function BehaviorPanel({ value }: { value: Dict }) {
  if (Object.keys(value).length === 0) return <Empty>Behavior summary is unavailable.</Empty>;
  const winRate = asDict(value.win_rate);
  const planCoverage = asDict(value.plan_coverage);
  const decisionCoverage = asDict(value.pre_fill_decision_coverage);
  const noActionReview = asDict(value.no_action_review_completion);
  return <>
    <div className="decision-metrics">
      <span>Closed Active Cycles<strong>{text(asDict(value.closed_active_trade_cycles).numerator, "0")}</strong></span>
      <span>Win Rate<strong>{behaviorPercent(winRate)}</strong><small>{text(winRate.numerator, "0")} / {text(winRate.denominator, "0")}</small></span>
      <span>Payoff Ratio<strong>{text(asDict(value.payoff_ratio).value)}</strong></span>
      <span>Plan Coverage<strong>{behaviorPercent(planCoverage)}</strong><small>{text(planCoverage.numerator, "0")} / {text(planCoverage.denominator, "0")}</small></span>
      <span>Pre-Fill Decision Coverage<strong>{behaviorPercent(decisionCoverage)}</strong></span>
      <span>No Action Reviews Completed<strong>{behaviorPercent(noActionReview)}</strong></span>
    </div>
    <details><summary>All Metrics, Denominators & Exclusions</summary><div className="table-wrap"><table><thead><tr><th>Metric</th><th>Value</th><th>Numerator / Denominator</th><th>Excluded</th><th>Availability</th></tr></thead><tbody>{BEHAVIOR_DETAILS.map((name) => { const metric = asDict(value[name]); return <tr key={name}><td>{name.split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ")}</td><td>{text(metric.value)}</td><td>{text(metric.numerator)} / {text(metric.denominator)}</td><td>{text(metric.excluded_count, "0")}</td><td>{metric.unavailable_reason ? text(metric.unavailable_reason) : <Badge value={metric.sample_sufficient === true ? "SUFFICIENT" : "LIMITED"} />}</td></tr>; })}</tbody></table></div></details>
  </>;
}

export default function DecisionWorkbenchPage() {
  const [requestedSubjectId, setRequestedSubjectId] = useState("");
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewInput, setReviewInput] = useState<ReviewInput | null>(null);
  const [reviewInputValue, setReviewInputValue] = useState("");
  const [reviewInputError, setReviewInputError] = useState<string | null>(null);
  const [decisionOpen, setDecisionOpen] = useState(false);
  const [decisionAction, setDecisionAction] = useState<DecisionAction>("no_action");
  const [decisionScenario, setDecisionScenario] = useState<DecisionScenario>("SIDEWAYS");
  const [decisionReason, setDecisionReason] = useState("");
  const [decisionReviewDate, setDecisionReviewDate] = useState(() => futureDateInput(7));
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [decisionMessage, setDecisionMessage] = useState<string | null>(null);
  const [captureRequested, setCaptureRequested] = useState(false);
  const [supersedesDecisionId, setSupersedesDecisionId] = useState<string | null>(null);
  const [journalTab, setJournalTab] = useState<JournalTab>("overview");
  const [cycleOffset, setCycleOffset] = useState(0);
  const [activityStatuses, setActivityStatuses] = useState<Record<string, ActivityStatus>>({});
  const [activityClassifications, setActivityClassifications] = useState<Record<string, ActivityClassification>>({});
  const [activityOrderLinks, setActivityOrderLinks] = useState<Record<string, string>>({});
  const [activityBusy, setActivityBusy] = useState<string | null>(null);
  const [activityError, setActivityError] = useState<string | null>(null);
  const [overrideOperation, setOverrideOperation] = useState<"SPLIT" | "MERGE" | "RELINK">("SPLIT");
  const [overrideRoot, setOverrideRoot] = useState("");
  const [overrideCycles, setOverrideCycles] = useState("");
  const [overrideActivities, setOverrideActivities] = useState("");
  const [overrideSplitGroups, setOverrideSplitGroups] = useState("");
  const [overrideTarget, setOverrideTarget] = useState("");
  const [overrideNote, setOverrideNote] = useState("");
  const [overridePreview, setOverridePreview] = useState<Dict | null>(null);
  const [overrideBusy, setOverrideBusy] = useState<"preview" | "apply" | null>(null);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [behaviorReviewBusy, setBehaviorReviewBusy] = useState(false);
  const [behaviorReviewError, setBehaviorReviewError] = useState<string | null>(null);
  const [behaviorClassification, setBehaviorClassification] = useState<ActivityClassification | "ALL">("ALL");
  const workbenchApi = useApi<Dict>(
    requestedSubjectId
      ? `/api/decision-workbench?subject_id=${encodeURIComponent(requestedSubjectId)}${behaviorClassification === "ALL" ? "" : `&classification=${encodeURIComponent(behaviorClassification)}`}`
      : `/api/decision-workbench${behaviorClassification === "ALL" ? "" : `?classification=${encodeURIComponent(behaviorClassification)}`}`,
  );
  const subjects = listOf<SubjectAggregate>(workbenchApi.data, "subjects");
  const activeSubjects = useMemo(
    () => subjects.filter((item) => upper(item.subject?.status) !== "ARCHIVED"),
    [subjects],
  );
  const defaultSubjectId = text(workbenchApi.data?.selected_subject_id, "");
  const subjectId = requestedSubjectId || defaultSubjectId;
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
  const orderIntentsUnavailable = partialFailures.includes("order_intents");
  const retroUnavailable = partialFailures.includes("retro");
  const scorecardsUnavailable = partialFailures.includes("scorecards");
  const reviewItemsUnavailable = partialFailures.includes("review_items");
  const reviewItems = listOf<Dict>(workbenchApi.data, "review_items");
  const behaviorReviewRuns = listOf<Dict>(workbenchApi.data, "behavior_review_runs");

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const requested = query.get("subject_id");
    if (requested) setRequestedSubjectId(requested);
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

  function selectJournalTab(value: JournalTab) {
    setJournalTab(value);
    window.history.replaceState(null, "", `#${value}`);
  }

  useEffect(() => {
    if (workbenchApi.loading) return;
    if (activeSubjects.length === 0) {
      setRequestedSubjectId("");
      return;
    }
    if (requestedSubjectId && !activeSubjects.some(
      (item) => text(item.subject?.subject_id, "") === requestedSubjectId
    )) {
      setRequestedSubjectId("");
    }
  }, [activeSubjects, requestedSubjectId, workbenchApi.loading]);

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
  const plan = state.current_trade_plan && typeof state.current_trade_plan === "object"
    ? state.current_trade_plan as Dict
    : null;
  const planId = text(plan?.plan_id, "");
  const planVersion = number(plan?.version);
  const planLinkReady = Boolean(planId && planVersion >= 1);
  const instrumentId = text(plan?.instrument_id, text(subject.primary_instrument_id, ""));
  const accountRows = listOf<Dict>(unwrap(asDict(workbenchApi.data?.accounts)), "accounts");
  const relatedPositions = accountRows.flatMap((account) =>
    listOf<Dict>(account, "positions")
      .filter((position) => text(position.instrument_id, "") === instrumentId)
      .map((position): Dict => ({ ...position, account_ref: account.account_ref, provider: account.provider })),
  );
  const allTransactions = listOf<Dict>(
    unwrap(asDict(workbenchApi.data?.transactions)),
    "transactions",
  );
  const relatedTransactions = allTransactions
    .filter((item) => text(item.instrument_id, "") === instrumentId)
    .sort((left, right) =>
      (Date.parse(text(right.occurred_at, "")) || 0)
      - (Date.parse(text(left.occurred_at, "")) || 0),
    );
  const relatedOrderIntents = listOf<Dict>(workbenchApi.data, "order_intents")
    .filter((item) => text(item.instrument_id, "") === instrumentId)
    .filter((item) => !item.case_id || text(item.case_id, "") === subjectId);
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
  const dailyEquityData = unwrap(asDict(workbenchApi.data?.daily_equity));
  const dailyEquityItems = listOf<Dict>(dailyEquityData, "items");
  const completeDailyEquity = dailyEquityItems.filter(
    (item) => upper(item.quality_status) === "COMPLETE",
  ).length;
  const activityAnnotations = listOf<Dict>(workbenchApi.data, "activity_annotations");
  const annotationByTransaction = new Map(activityAnnotations.map((item) => [
    `${text(item.provider, "")}:${text(item.account_ref, "")}:${text(item.provider_transaction_id, "")}`,
    item,
  ]));
  const unlinkedActivities = listOf<Dict>(asDict(workbenchApi.data?.unlinked_activity), "activities");
  const relatedUnlinkedActivities = unlinkedActivities.filter((item) =>
    text(asDict(item.transaction).instrument_id, "") === instrumentId
  );
  const tradeCycleData = unwrap(asDict(workbenchApi.data?.trade_cycles));
  const tradeCycles = listOf<Dict>(tradeCycleData, "cycles");
  const tradeCycleOverrides = listOf<Dict>(tradeCycleData, "override_revisions");
  const overrideSelectedCycleIds = overrideCycles.split(",").map((item) => item.trim()).filter(Boolean);
  const overrideActivityOptions = tradeCycles
    .filter((cycle) => overrideSelectedCycleIds.includes(text(cycle.cycle_id, "")) || (!overrideSelectedCycleIds.length && text(cycle.cycle_id, "") === overrideRoot))
    .flatMap((cycle) => listOf<string>(cycle, "activity_ids"));
  const latestTradeCycle = tradeCycles[0] ?? null;
  const tradeCycleProjectionIncomplete = upper(tradeCycleData.status) !== "COMPLETE";
  const cyclePageSize = 6;
  const visibleTradeCycles = tradeCycles.slice(cycleOffset, cycleOffset + cyclePageSize);
  const journalTimelineRows = [
    ...timelineItems.map((item) => ({
      key: text(item.entity_id),
      kind: upper(item.entity_type),
      title: text(item.title),
      detail: text(item.summary),
      occurredAt: text(item.occurred_at, ""),
    })),
    ...relatedTransactions.map((item) => {
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

  useEffect(() => {
    if (cycleOffset >= tradeCycles.length && cycleOffset !== 0) setCycleOffset(0);
  }, [cycleOffset, tradeCycles.length]);

  useEffect(() => {
    if (!captureRequested || !selected) return;
    setDecisionError(null);
    setDecisionOpen(true);
    setCaptureRequested(false);
    const url = new URL(window.location.href);
    url.searchParams.delete("capture");
    url.searchParams.delete("supersedes_decision_id");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, [captureRequested, selected]);

  async function classifyActivity(item: Dict) {
    const transaction = asDict(item.transaction);
    const key = text(item.source_key, text(transaction.provider_transaction_id));
    const status = activityStatuses[key] ?? (planLinkReady && latestDecision ? "LINKED_DECISION_PLAN" : "UNPLANNED");
    setActivityBusy(key); setActivityError(null);
    try {
      const linked = status === "LINKED_DECISION_PLAN";
      const defaultClassification: ActivityClassification = status === "CASH_MANAGEMENT" ? "CASH_MANAGEMENT" : status === "TRANSFER_OR_CORPORATE_ACTION" ? "TRANSFER_OR_ADMIN" : linked ? "ACTIVE_TRADE" : "UNCLASSIFIED";
      await postApi<Dict>("/api/activity-annotations", {
        provider: transaction.provider,
        account_ref: transaction.account_ref,
        provider_transaction_id: transaction.provider_transaction_id,
        status,
        classification: activityClassifications[key] ?? defaultClassification,
        order_intent_id: activityOrderLinks[key] || null,
        decision_id: linked ? latestDecision?.entity_id : undefined,
        trade_plan_id: linked ? planId : undefined,
        trade_plan_version: linked ? planVersion : undefined,
        subject_id: linked ? subjectId : undefined,
        expected_version: 0,
        idempotency_key: `console-activity-annotation-${crypto.randomUUID()}`,
        authorization_note: `User classified the exact Broker activity as ${status} in Journal.`,
      });
      workbenchApi.refresh();
    } catch (cause) {
      setActivityError(cause instanceof Error ? cause.message : "Activity classification failed");
    } finally { setActivityBusy(null); }
  }

  function overridePayload() {
    const csv = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
    const root = overrideRoot.trim();
    const cycles = csv(overrideCycles);
    const latestVersion = tradeCycleOverrides
      .filter((item) => text(item.root_cycle_id, "") === root)
      .reduce((current, item) => Math.max(current, number(item.version)), 0);
    return {
      root_cycle_id: root,
      operation: overrideOperation,
      cycle_ids: cycles.length > 0 ? cycles : [root],
      activity_ids: csv(overrideActivities),
      split_groups: overrideSplitGroups.split(/\n|;/).map(csv).filter((group) => group.length > 0),
      target_cycle_id: overrideTarget.trim() || null,
      note: overrideNote.trim() || null,
      expected_version: latestVersion,
      idempotency_key: `console-trade-cycle-override-${crypto.randomUUID()}`,
      authorization_note: `User confirmed the ${overrideOperation} impact for the exact Trade Cycle projection.`,
    };
  }

  function updateSplitGroup(index: number, values: string[]) {
    const groups = overrideSplitGroups.split(/\n|;/).map((value) => value.trim()).filter(Boolean);
    while (groups.length < 2) groups.push("");
    groups[index] = values.join(",");
    setOverrideSplitGroups(groups.join(";")); setOverridePreview(null);
  }

  async function previewCycleOverride() {
    setOverrideBusy("preview"); setOverrideError(null); setOverridePreview(null);
    try {
      const value = await postApi<Dict>("/api/trade-cycle-overrides/preview", overridePayload());
      setOverridePreview(value);
    } catch (cause) {
      setOverrideError(cause instanceof Error ? cause.message : "Trade Cycle preview failed");
    } finally { setOverrideBusy(null); }
  }

  async function applyCycleOverride() {
    if (!overridePreview) { setOverrideError("Preview the exact impact before applying it."); return; }
    setOverrideBusy("apply"); setOverrideError(null);
    try {
      await postApi<Dict>("/api/trade-cycle-overrides", overridePayload());
      setOverridePreview(null); workbenchApi.refresh();
    } catch (cause) {
      setOverrideError(cause instanceof Error ? cause.message : "Trade Cycle override failed");
    } finally { setOverrideBusy(null); }
  }

  async function runWeeklyBehaviorReview() {
    setBehaviorReviewBusy(true); setBehaviorReviewError(null);
    try {
      const now = new Date();
      const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
      const daysSinceMonday = (end.getUTCDay() + 6) % 7;
      end.setUTCDate(end.getUTCDate() - daysSinceMonday);
      const start = new Date(end); start.setUTCDate(start.getUTCDate() - 7);
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
        instrument_ids: instrumentId ? [instrumentId] : [],
        cycle_ids: tradeCycles.map((item) => text(item.cycle_id)),
        decision_ids: decisionItems.map((item) => text(item.entity_id)),
        retro_run_ids: latestRetro?.run_id ? [text(latestRetro.run_id)] : [],
        retro_review_ids: retroReview.review_id ? [text(retroReview.review_id)] : [],
        review_item_source_keys: reviewItems.map((item) => text(item.source_key, "")).filter(Boolean),
        subject_ids: [subjectId],
        action_items: actionItems,
        source_read_complete: !retroUnavailable && !reviewItemsUnavailable,
        source_error_code: retroUnavailable || reviewItemsUnavailable ? "BEHAVIOR_REVIEW_SOURCE_UNAVAILABLE" : null,
        idempotency_key: `console-behavior-review-${subjectId}-${start.toISOString().slice(0, 10)}`,
      });
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

  const relatedRetro = retroRuns(workbenchApi.data).filter((run) => {
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
    nextSteps.push({ key: "retro", severity: "REVIEW", title: `Trade Retro is ${retroReviewStatus.toLowerCase()}`, detail: "Review deterministic findings and record follow-up actions.", href: "/retro" });
  }
  if (reviewItemsUnavailable && latestScorecard && scorecardGaps.length > 0) {
    nextSteps.push({ key: "scorecard", severity: "GAP", title: `${scorecardGaps.length} Scorecard dimension gap(s)`, detail: "Inspect evidence quality and repeated discipline gaps before revising judgment.", href: `/scorecards?subject_id=${encodeURIComponent(subjectId)}` });
  }
  if (!reviewItemsUnavailable) {
    nextSteps.push(...reviewItems.map((item) => ({
      key: text(item.source_key, text(item.review_item_id)),
      severity: text(item.severity, "ATTENTION"),
      title: text(item.title, "Review required"),
      detail: `${text(item.detail, "Inspect the durable source.")} · ${upper(item.status)}`,
      href: text(item.href, "/"),
      reviewItem: item,
    })));
  }
  if (partialFailures.length > 0) {
    nextSteps.unshift({ key: "incomplete-context", severity: "UNAVAILABLE", title: "Decision context is incomplete", detail: `Retry before interpreting missing sections: ${partialFailures.join(", ")}.`, href: "#decision-context-status" });
  }

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
    void persistReviewItem(item, status);
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
      setDecisionOpen(false);
      setDecisionReason("");
      setDecisionReviewDate(futureDateInput(7));
      setSupersedesDecisionId(null);
      setDecisionMessage(`${decisionScenario} · ${selectedAction.replaceAll("_", " ")} recorded.`);
      workbenchApi.refresh();
    } catch (cause) {
      setDecisionError(cause instanceof Error ? cause.message : "Decision Record failed.");
    } finally {
      setDecisionBusy(false);
    }
  }

  const loading = workbenchApi.loading;
  const error = workbenchApi.error;

  return <ConsoleShell active="decision-workbench" pageActions={<PageActionMenu ariaLabel="Journal Page Actions" items={[
    { id: "record-decision", label: "Record Decision", description: "Reuse the current Thesis and Trade Plan context", icon: <ClipboardPenLine aria-hidden="true" />, disabled: !selected || loading, onSelect: () => { setSupersedesDecisionId(null); setDecisionError(null); setDecisionOpen(true); } },
    { id: "refresh", label: loading ? "Refreshing…" : "Refresh", description: "Reload durable workflow context", icon: <RefreshCw aria-hidden="true" className={loading ? "spin" : undefined} />, disabled: loading, onSelect: workbenchApi.refresh },
  ]} />}>
    <DataBoundary loading={loading} error={error}>
      <div className="decision-workbench">
        <div className="workspace-controls decision-toolbar"><label className="decision-subject-picker"><span>Research Subject</span><select value={subjectId} onChange={(event) => setRequestedSubjectId(event.target.value)}>{activeSubjects.map((item) => <option key={text(item.subject?.subject_id)} value={text(item.subject?.subject_id)}>{text(item.subject?.title)} · {shortId(item.subject?.primary_instrument_id)}</option>)}</select></label></div>

        {!selected ? <Empty>Create or activate a Research Subject in the existing Research workspace first.</Empty> : <>
          <section className="decision-subject-hero">
            <div><span>{upper(subject.subject_type, "RESEARCH SUBJECT")}</span><h2>{text(subject.title, "Unnamed Research Subject")}</h2><p>{text(subject.summary, "No stable research scope recorded.")}</p></div>
            <div className="decision-subject-meta"><Badge value={upper(subject.status)} /><strong>{shortId(subject.primary_instrument_id)}</strong><small className="mono">{subjectId}</small></div>
          </section>

          <HorizontalTabs items={JOURNAL_TABS} value={journalTab} onChange={selectJournalTab} ariaLabel="Journal Sections" idPrefix="journal-tab" panelIdPrefix="journal-panel" />

          <section id="journal-panel-overview" role="tabpanel" aria-labelledby="journal-tab-overview" hidden={journalTab !== "overview"} className="journal-panel-stack">
          <Card kicker="DATA CONFIDENCE" title="Coverage" action={<Badge value={partialFailures.length || transactionWindowLimited || tradeCycleProjectionIncomplete ? "PARTIAL" : "AVAILABLE"} />}>
            <div className="decision-metrics"><span>Account Facts<strong>{accountsUnavailable ? "Unavailable" : "Durable"}</strong></span><span>Activity Window<strong>{transactionsUnavailable ? "Unavailable" : transactionWindowLimited ? "Limited" : "Visible"}</strong></span><span>Order Lifecycle<strong>{orderIntentsUnavailable ? "Unavailable" : `${relatedOrderIntents.length} Visible`}</strong></span><span>Daily Equity<strong>{dailyEquityItems.length === 0 ? "Unavailable" : `${completeDailyEquity} / ${dailyEquityItems.length} Complete`}</strong></span><span>Cycle Projection<strong>{tradeCyclesUnavailable ? "Unavailable" : text(tradeCycleData.status, "Unknown")}</strong></span></div>
          </Card>
          <Card kicker="PERFORMANCE · NATIVE CURRENCY" title="Year-To-Date Returns" action={<Link href="/portfolio#performance">Portfolio Details →</Link>}>
            {performanceSeries.length === 0 ? <Empty>No trustworthy account return series is available.</Empty> : <div className="decision-metrics">{performanceSeries.map((item, index) => <span key={`${text(item.account_ref)}-${text(item.currency)}-${index}`}>{text(item.currency)} · {shortId(item.account_ref)}<strong>{item.twr == null ? "TWR —" : `TWR ${(number(item.twr) * 100).toFixed(2)}%`}</strong><small>MWR {item.xirr == null ? "—" : `${(number(item.xirr) * 100).toFixed(2)}%`} · Drawdown {item.maximum_drawdown == null ? "—" : `${(number(item.maximum_drawdown) * 100).toFixed(2)}%`} · {text(item.status)}</small></span>)}</div>}
          </Card>
          <Card kicker="P/L COMPOSITION · DURABLE FACTS" title="Income, Fees & Closed Cycles">
            {performanceSeries.length === 0 ? <Empty>P/L composition is unavailable with the current coverage.</Empty> : <div className="decision-metrics">{performanceSeries.map((item, index) => <span key={`composition-${text(item.account_ref)}-${index}`}>{text(item.currency)}<strong>Income {text(item.dividends, "0")} + {text(item.interest, "0")}</strong><small>Known Fees {text(item.known_fees, "—")} · {listOf<Dict>(item, "cycle_performance").length} closed Cycle return(s)</small></span>)}</div>}
          </Card>
          <Card kicker="BEHAVIOR SNAPSHOT · NO SCORE" title="Current Cohort" action={<button type="button" onClick={() => selectJournalTab("behavior")}>Open Behavior</button>}>
            <div className="decision-metrics"><span>Closed Active Cycles<strong>{text(asDict(behaviorData.closed_active_trade_cycles).numerator, "0")}</strong></span><span>Win Rate<strong>{behaviorPercent(asDict(behaviorData.win_rate))}</strong></span><span>Plan Coverage<strong>{behaviorPercent(asDict(behaviorData.plan_coverage))}</strong></span><span>Pre-Fill Decision Coverage<strong>{behaviorPercent(asDict(behaviorData.pre_fill_decision_coverage))}</strong></span></div>
          </Card>
          <Card className="decision-next-card" kicker="WORKFLOW" title="Needs Attention" description="Only unresolved steps appear here. Catalyst, Scorecard, and Retro remain evidence sources instead of separate tasks to visit." action={<div className="page-actions"><Badge value={partialFailures.length ? "INCOMPLETE" : nextSteps.length ? `${nextSteps.length} ITEMS` : "READY"} /><Link href="/#review-queue">All Durable Items →</Link></div>}><StepList items={nextSteps} busy={reviewBusy} onTransition={(item, status) => { void transitionReviewItem(item, status); }} /><ErrorNote>{reviewError}</ErrorNote>{decisionMessage ? <div className="inline-success">{decisionMessage}</div> : null}</Card>

          <div className="decision-stage-grid">
            <Card kicker="1 · DECIDE" title="Judgment & Plan" action={<div className="workflow-card-actions"><ActionButton onClick={() => { setSupersedesDecisionId(null); setDecisionError(null); setDecisionOpen(true); }}>Record Decision</ActionButton><Link href={`/research#subject-${subjectId}`}>Open Research →</Link></div>}>
              <div className="decision-stage-lead"><Badge value={researchUnavailable || timelineUnavailable ? "INCOMPLETE" : latestDecision ? "DECISION" : primaryThesis ? upper(primaryThesis.status) : "MISSING"} /><strong>{researchUnavailable ? "Research state read unavailable" : timelineUnavailable ? "Decision Timeline unavailable" : text(latestDecision?.title, text(primaryThesis?.title, "No live Thesis"))}</strong><p>{researchUnavailable || timelineUnavailable ? "Retry the missing durable read before interpreting the decision chain." : text(latestDecision?.summary, text(primaryRevision?.statement, "Create a falsifiable judgment before defining execution intent."))}</p></div>
              <div className="workflow-support-line"><span>Trade Plan</span><Badge value={researchUnavailable ? "UNAVAILABLE" : plan ? upper(plan.status) : "MISSING"} /><strong>{plan ? `${shortId(plan.instrument_id)} · v${text(plan.version, "—")}` : "No Current Plan"}</strong></div>
              <div className="decision-metrics"><span>Recent Decisions<strong>{decisionItems.length}</strong></span><span>Plan Conditions<strong>{listOf<Dict>(plan, "conditions").length}</strong></span><span>Pending Reviews<strong>{pendingCandidates.length + openQuestions.length}</strong></span></div>
            </Card>

            <Card kicker="2 · OBSERVE" title="Evidence & Triggers" action={<Link href="/monitors">Open Monitors →</Link>}>
              <div className="decision-stage-lead"><Badge value={monitorsUnavailable ? "UNAVAILABLE" : activeMonitors.length ? "ACTIVE" : "UNCOVERED"} /><strong>{monitorsUnavailable ? "Monitor dashboard read unavailable" : `${linkedMonitors.length} linked · ${activeMonitors.length} active`}</strong><p>{monitorsUnavailable ? "Coverage is unknown; absence must not be interpreted as no Monitor." : latestMonitorRun ? `Latest run ${upper(latestMonitorRun.status)} · ${formatDate(latestMonitorRun.completed_at ?? latestMonitorRun.started_at)}` : "No linked Monitor run has been recorded."}</p></div>
              <div className="workflow-support-line"><span>Next Catalyst</span><Badge value={agendaUnavailable ? "UNAVAILABLE" : overdueCatalysts.length ? "OVERDUE" : "TRACKING"} /><strong>{agendaUnavailable ? "Agenda Unavailable" : upcomingCatalysts[0] ? `${text(upcomingCatalysts[0].title)} · ${formatDate(upcomingCatalysts[0].window_start)}` : "No Upcoming Catalyst"}</strong></div>
              <div className="decision-metrics"><span>Triggered Rules<strong>{triggeredRules.length}</strong></span><span>Upcoming Events<strong>{upcomingCatalysts.length}</strong></span><span>Overdue Outcomes<strong>{overdueCatalysts.length}</strong></span></div>
            </Card>

            <Card kicker="3 · EXECUTE" title="Position & Trade Cycles" action={<Link href="/portfolio">Open Portfolio →</Link>}>
              <div className="decision-stage-lead"><Badge value={accountsUnavailable || transactionsUnavailable || tradeCyclesUnavailable ? "INCOMPLETE" : transactionWindowLimited || tradeCycleProjectionIncomplete ? "PARTIAL" : relatedPositions.length ? "HELD" : relatedTransactions.length ? "CLOSED / ACTIVITY" : "NO ACTIVITY"} /><strong>{instrumentId ? shortId(instrumentId) : "No Execution Instrument"}</strong><p>{transactionsUnavailable ? "Durable transaction history is unavailable; do not interpret the empty state as no trading." : latestTransaction ? `${upper(latestTransaction.side, upper(latestTransaction.kind))} · ${formatDate(latestTransaction.occurred_at)} · ${text(latestTransaction.quantity, "—")} @ ${text(latestTransaction.price, "—")}` : "No durable activity is linked to this execution Instrument."}</p></div>
              <div className="workflow-support-line"><span>Latest Trade Cycle</span><Badge value={tradeCyclesUnavailable ? "UNAVAILABLE" : upper(latestTradeCycle?.status, "NOT READY")} /><strong>{tradeCyclesUnavailable ? "Cycle projection is unavailable" : latestTradeCycle ? `${formatDate(latestTradeCycle.opened_at)} → ${latestTradeCycle.closed_at ? formatDate(latestTradeCycle.closed_at) : "Open"} · Net P/L ${text(latestTradeCycle.net_realized_pnl, "Unavailable")} ${text(latestTradeCycle.currency, "")}${tradeCycleProjectionIncomplete ? " · Coverage Incomplete" : ""}` : relatedTransactions.length ? "No resolvable long-only Cycle; inspect durable activity coverage" : "A Fill is required before a Cycle can open"}</strong></div>
              <div className="decision-metrics"><span>Held Quantity<strong>{heldQuantity || "—"}</strong></span><span>Trade Cycles<strong>{tradeCycles.length}</strong></span><span>Visible Activity<strong>{relatedTransactions.length}</strong></span></div>
            </Card>

            <Card kicker="4 · REVIEW" title="Performance & Behavior" action={<div className="workflow-card-actions"><Link href="/portfolio">Performance →</Link><Link href="/retro">Review Details →</Link></div>}>
              <div className="decision-stage-lead"><Badge value={retroUnavailable ? "UNAVAILABLE" : latestRetro ? retroReviewStatus : "NO RUN"} /><strong>{retroUnavailable ? "Trade Retro history read unavailable" : latestRetro ? `${text(latestRetro.period_start).slice(0, 10)} → ${text(latestRetro.period_end).slice(0, 10)}` : "No related review"}</strong><p>{retroUnavailable ? "Review status and findings are unknown until the durable read recovers." : latestRetro ? `${listOf<Dict>(latestRetro, "findings").length} deterministic finding(s) · generated ${formatDate(latestRetro.generated_at)}` : "Performance remains in Portfolio; behavior review begins after durable transactions and a pre-period plan snapshot exist."}</p></div>
              <div className="workflow-support-line"><span>Judgment Calibration</span><Badge value={scorecardsUnavailable ? "UNAVAILABLE" : scorecardGaps.length ? "GAPS" : latestScorecard ? "AVAILABLE" : "NO RUN"} /><strong>{latestScorecard ? `${scorecardGaps.length} gap(s) across ${listOf<Dict>(latestScorecard, "dimensions").length} dimensions` : "No Calibration Evidence Yet"}</strong></div>
              <div className="decision-metrics"><span>Retro Findings<strong>{listOf<Dict>(latestRetro, "findings").length}</strong></span><span>Follow-Up Actions<strong>{listOf<string>(retroReview, "action_items").length}</strong></span><span>Calibration Gaps<strong>{scorecardGaps.length}</strong></span></div>
            </Card>
          </div>
          </section>

          <section id="journal-panel-timeline" role="tabpanel" aria-labelledby="journal-tab-timeline" hidden={journalTab !== "timeline"} className="journal-panel-stack">
            <Card kicker="RECONCILE · APPEND ONLY" title="Unlinked Activity" action={<Badge value={`${relatedUnlinkedActivities.length} ITEMS`} />}>
              <p className="card-note">Each unmatched Broker trade can be linked to the current exact Decision and Plan, or truthfully classified without inventing a pre-trade reason. Saving closes its durable Review item.</p>
              {relatedUnlinkedActivities.length === 0 ? <Empty>No unlinked Broker trades for this execution Instrument.</Empty> : <div className="journal-cycle-list">{relatedUnlinkedActivities.map((item) => { const transaction = asDict(item.transaction); const key = text(item.source_key, text(transaction.provider_transaction_id)); const defaultStatus: ActivityStatus = planLinkReady && latestDecision ? "LINKED_DECISION_PLAN" : "UNPLANNED"; const defaultClassification: ActivityClassification = defaultStatus === "LINKED_DECISION_PLAN" ? "ACTIVE_TRADE" : "UNCLASSIFIED"; return <article key={key}><header><div><strong>{upper(transaction.side)} · {shortId(transaction.instrument_id)}</strong><small>{formatDate(transaction.occurred_at)} · {text(transaction.quantity)} @ {text(transaction.price)} {text(transaction.currency, "")}</small></div><Badge value="UNLINKED" /></header><div className="portfolio-form-actions"><select aria-label="Activity Classification" value={activityStatuses[key] ?? defaultStatus} onChange={(event) => setActivityStatuses((current) => ({ ...current, [key]: event.target.value as ActivityStatus }))}><option value="LINKED_DECISION_PLAN" disabled={!planLinkReady || !latestDecision}>Link Current Decision & Plan</option><option value="UNPLANNED">Unplanned</option><option value="CASH_MANAGEMENT">Cash Management</option><option value="TRANSFER_OR_CORPORATE_ACTION">Transfer / Corporate Action</option><option value="PROVIDER_CORRECTION">Provider Correction</option></select><select aria-label="Activity Classification Type" value={activityClassifications[key] ?? defaultClassification} onChange={(event) => setActivityClassifications((current) => ({ ...current, [key]: event.target.value as ActivityClassification }))}><option value="ACTIVE_TRADE">Active Trade</option><option value="LONG_TERM_INVESTMENT">Long-Term Investment</option><option value="HEDGE">Hedge</option><option value="CASH_MANAGEMENT">Cash Management</option><option value="TRANSFER_OR_ADMIN">Transfer or Admin</option><option value="UNCLASSIFIED">Unclassified</option></select><select aria-label="Linked Order Intent" value={activityOrderLinks[key] || ""} onChange={(event) => setActivityOrderLinks((current) => ({ ...current, [key]: event.target.value }))}><option value="">No Exact Order Link</option>{relatedOrderIntents.map((order) => <option value={String(order.order_intent_id)} key={String(order.order_intent_id)}>{String(order.instruction)} · {formatDate(order.created_at)} · {String(order.status)}</option>)}</select><ActionButton busy={activityBusy === key} onClick={() => { void classifyActivity(item); }}>Save Classification</ActionButton></div></article>; })}</div>}
              <ErrorNote>{activityError}</ErrorNote>
            </Card>
            <Card kicker="DECISIONS + ACTIVITY" title="Timeline" action={<Badge value={`${journalTimelineRows.length} ITEMS`} />}>
              <p className="card-note">Research Decisions, Trading Partner order intents/results, and durable Broker activities share one chronological view. Missing Provider history or an absent exact link remains a coverage gap; this page never refreshes a Broker.</p>
              {journalTimelineRows.length === 0 ? <Empty>No Decision or durable activity is available for this Research Subject.</Empty> : <div className="journal-timeline-list">{journalTimelineRows.slice(0, 50).map((item) => <article key={`${item.kind}-${item.key}`}><Badge value={item.kind} /><div><header><strong>{item.title}</strong><time>{formatDate(item.occurredAt)}</time></header><p>{item.detail}</p></div></article>)}</div>}
            </Card>
          </section>

          <section id="journal-panel-cycles" role="tabpanel" aria-labelledby="journal-tab-cycles" hidden={journalTab !== "cycles"} className="journal-panel-stack">
            <Card kicker="DETERMINISTIC · LONG-ONLY" title="Trade Cycles" action={<div className="page-actions"><Badge value={text(tradeCycleData.status, "UNKNOWN")} />{tradeCycleOverrides.length > 0 ? <Badge value={`${tradeCycleOverrides.length} OVERRIDES`} /> : null}<Link href="/portfolio#activity">Portfolio Details →</Link></div>}>
              <p className="card-note">Cycles are rebuilt from durable transactions by account, Instrument, and native currency. Append-only split/merge/relink revisions change only the effective projection; the original algorithm Cycles remain retained.</p>
              {tradeCycles.length === 0 ? <Empty>No resolvable Trade Cycle is available for this execution Instrument.</Empty> : <><div className="journal-cycle-list">{visibleTradeCycles.map((cycle) => <article key={text(cycle.cycle_id)}><header><div><strong>{shortId(cycle.instrument_id)}</strong><small>{formatDate(cycle.opened_at)} → {cycle.closed_at ? formatDate(cycle.closed_at) : "Open"}</small></div><div className="page-actions"><Badge value={text(cycle.classification, "UNCLASSIFIED")} /><Badge value={text(cycle.status, "UNKNOWN")} /></div></header><dl><div><dt>Net P/L</dt><dd>{text(cycle.net_realized_pnl)} {text(cycle.currency, "")}</dd></div><div><dt>Ending Quantity</dt><dd>{text(cycle.ending_quantity)}</dd></div><div><dt>Adds / Reductions</dt><dd>{text(cycle.add_count, "0")} / {text(cycle.reduce_count, "0")}</dd></div><div><dt>Quality</dt><dd>{text(cycle.quality)}</dd></div></dl></article>)}</div><Paginator step={cyclePageSize} offset={cycleOffset} hasMore={cycleOffset + cyclePageSize < tradeCycles.length} onOffsetChange={setCycleOffset} summary={<small>{cycleOffset + 1}–{Math.min(cycleOffset + cyclePageSize, tradeCycles.length)} of {tradeCycles.length}</small>} /></>}
              <details><summary>Split, Merge, or Relink Cycles</summary><p className="card-note">Preview is mandatory. Choose visible Cycles and activities; no opaque ID needs to be copied. Applying appends a revision and preserves the original algorithm projection.</p><div className="portfolio-form-grid"><FormField label="Operation" required><select required value={overrideOperation} onChange={(event) => { setOverrideOperation(event.target.value as "SPLIT" | "MERGE" | "RELINK"); setOverridePreview(null); }}><option value="SPLIT">Split</option><option value="MERGE">Merge</option><option value="RELINK">Relink</option></select></FormField><FormField label="Root Cycle" required><select required value={overrideRoot} onChange={(event) => { setOverrideRoot(event.target.value); setOverrideCycles(event.target.value); setOverridePreview(null); }}><option value="">Select a Cycle</option>{tradeCycles.map((cycle) => <option value={text(cycle.cycle_id)} key={`root-${text(cycle.cycle_id)}`}>{shortId(cycle.instrument_id)} · {formatDate(cycle.opened_at)} → {cycle.closed_at ? formatDate(cycle.closed_at) : "Open"}</option>)}</select></FormField><FormField label="Source Cycles" required><select required multiple value={overrideSelectedCycleIds} onChange={(event) => { setOverrideCycles(Array.from(event.currentTarget.selectedOptions, (option) => option.value).join(",")); setOverridePreview(null); }}>{tradeCycles.map((cycle) => <option value={text(cycle.cycle_id)} key={`source-${text(cycle.cycle_id)}`}>{shortId(cycle.instrument_id)} · {formatDate(cycle.opened_at)} → {cycle.closed_at ? formatDate(cycle.closed_at) : "Open"}</option>)}</select></FormField>{overrideOperation === "SPLIT" ? <><FormField label="Split Group A" required><select required multiple onChange={(event) => updateSplitGroup(0, Array.from(event.currentTarget.selectedOptions, (option) => option.value))}>{overrideActivityOptions.map((activity) => <option value={activity} key={`a-${activity}`}>{activity}</option>)}</select></FormField><FormField label="Split Group B" required><select required multiple onChange={(event) => updateSplitGroup(1, Array.from(event.currentTarget.selectedOptions, (option) => option.value))}>{overrideActivityOptions.map((activity) => <option value={activity} key={`b-${activity}`}>{activity}</option>)}</select></FormField></> : null}{overrideOperation === "RELINK" ? <><FormField label="Activities to Move" required><select required multiple onChange={(event) => { setOverrideActivities(Array.from(event.currentTarget.selectedOptions, (option) => option.value).join(",")); setOverridePreview(null); }}>{overrideActivityOptions.map((activity) => <option value={activity} key={`move-${activity}`}>{activity}</option>)}</select></FormField><FormField label="Target Cycle" required><select required value={overrideTarget} onChange={(event) => { setOverrideTarget(event.target.value); setOverridePreview(null); }}><option value="">Select a Target</option>{tradeCycles.map((cycle) => <option value={text(cycle.cycle_id)} key={`target-${text(cycle.cycle_id)}`}>{shortId(cycle.instrument_id)} · {formatDate(cycle.opened_at)}</option>)}</select></FormField></> : null}<FormField label="Revision Note"><input value={overrideNote} onChange={(event) => { setOverrideNote(event.target.value); setOverridePreview(null); }} /></FormField></div><div className="portfolio-form-actions"><ActionButton busy={overrideBusy === "preview"} onClick={() => { void previewCycleOverride(); }}>Preview Impact</ActionButton><ActionButton busy={overrideBusy === "apply"} disabled={!overridePreview} onClick={() => { void applyCycleOverride(); }}>Apply Revision</ActionButton></div>{overridePreview ? <div className="inline-success">Preview ready · {listOf<Dict>(overridePreview, "impacts").length} impact(s) · original algorithm projection retained.</div> : null}<ErrorNote>{overrideError}</ErrorNote></details>
            </Card>
          </section>

          <section id="journal-panel-behavior" role="tabpanel" aria-labelledby="journal-tab-behavior" hidden={journalTab !== "behavior"} className="journal-panel-stack">
            <Card kicker="DISCIPLINE · DETERMINISTIC" title="Behavior" action={<div className="page-actions"><select aria-label="Behavior Classification" value={behaviorClassification} onChange={(event) => setBehaviorClassification(event.target.value as ActivityClassification | "ALL")}><option value="ALL">All Classifications</option><option value="ACTIVE_TRADE">Active Trade</option><option value="LONG_TERM_INVESTMENT">Long-Term Investment</option><option value="HEDGE">Hedge</option><option value="CASH_MANAGEMENT">Cash Management</option><option value="TRANSFER_OR_ADMIN">Transfer or Admin</option><option value="UNCLASSIFIED">Unclassified</option></select><Badge value={text(behaviorData.algorithm_version, "UNAVAILABLE")} /></div>}>
              <p className="card-note">No aggregate score. Every ratio retains its denominator, exclusions, and exact Cycle or Decision references. Open, unresolved, mixed-currency monetary, and SGOV cash-management Cycles are excluded where required.</p>
              <BehaviorPanel value={behaviorData} />
            </Card>
          </section>

          <section id="journal-panel-reviews" role="tabpanel" aria-labelledby="journal-tab-reviews" hidden={journalTab !== "reviews"} className="journal-panel-stack">
            <Card kicker="FOLLOW-UP" title="Reviews" action={<div className="workflow-card-actions"><ActionButton busy={behaviorReviewBusy} onClick={() => { void runWeeklyBehaviorReview(); }}>Run Weekly Review</ActionButton><Link href="/retro">Trade Retro →</Link><Link href={`/scorecards?subject_id=${encodeURIComponent(subjectId)}`}>Calibration →</Link></div>}>
              <div className="decision-metrics"><span>Open Items<strong>{nextSteps.length}</strong></span><span>Retro Findings<strong>{listOf<Dict>(latestRetro, "findings").length}</strong></span><span>Calibration Gaps<strong>{scorecardGaps.length}</strong></span></div>
              <StepList items={nextSteps} busy={reviewBusy} onTransition={(item, status) => { void transitionReviewItem(item, status); }} />
              <ErrorNote>{behaviorReviewError}</ErrorNote>
              {behaviorReviewRuns.length > 0 ? <div className="journal-cycle-list">{behaviorReviewRuns.slice(0, 8).map((run) => <article key={text(run.run_id)}><header><div><strong>{text(asDict(run.cohort).period_kind)} · {text(asDict(run.cohort).period_start).slice(0, 10)}</strong><small>{formatDate(run.generated_at)}</small></div><Badge value={text(run.status)} /></header><div className="page-actions">{listOf<Dict>(run, "action_observations").map((item) => <Badge key={text(item.observation_id)} value={text(item.status)} />)}</div><small className="table-sub">NEW, PERSISTENT, RESOLVED, and RECURRED are derived only from complete durable action-source reads.</small></article>)}</div> : <Empty>No period Behavior Review has been recorded yet.</Empty>}
            </Card>
          </section>
        </>}
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
          open={decisionOpen}
          title="Record Decision"
          description={supersedesDecisionId ? `Review and supersede ${shortId(supersedesDecisionId)} with a new durable Decision. This does not submit or authorize an order.` : "Save the current strategy decision in the existing durable Decision Record. This does not submit or authorize an order."}
          confirmLabel="Save Decision"
          busy={decisionBusy}
          onConfirm={() => { void saveDecision(); }}
          onCancel={() => { if (!decisionBusy) { setDecisionOpen(false); setSupersedesDecisionId(null); } }}
        >
          <div className="journal-capture-form">
            <FormField label="Action" required><select required value={decisionAction} onChange={(event) => setDecisionAction(event.target.value as DecisionAction)}>{DECISION_ACTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></FormField>
            <FormField label="Current Scenario" required><select required value={decisionScenario} onChange={(event) => setDecisionScenario(event.target.value as DecisionScenario)}>{DECISION_SCENARIOS.map((scenario) => <option key={scenario} value={scenario}>{scenario}</option>)}</select></FormField>
            <FormField label="Reason" required className="journal-capture-wide"><textarea required value={decisionReason} onChange={(event) => { setDecisionReason(event.target.value); setDecisionError(null); }} placeholder="What fact, structure, or risk constraint supports this decision?" /></FormField>
            <FormField label="Review Date" className="journal-capture-wide"><input type="date" value={decisionReviewDate} onChange={(event) => setDecisionReviewDate(event.target.value)} /></FormField>
            <p className="card-note journal-capture-wide">Strategy is recorded as strategy_v1{planLinkReady ? ` and linked to Trade Plan v${planVersion}` : ""}. The selected scenario records this action; the other scenarios remain REVIEW under the current confirmed Plan.</p>
            <ErrorNote role="alert">{decisionError}</ErrorNote>
          </div>
        </ConfirmationDialog>
      </div>
    </DataBoundary>
  </ConsoleShell>;
}
