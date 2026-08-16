"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ActionButton, Badge, Card, DataBoundary, Empty, RefreshButton, formatDate, shortId } from "../components/ui";
import { ConsoleShell } from "../components/console-shell";
import { envelopeData, listOf, postApi, useApi } from "../lib/api";
import { useAgentPageContext } from "../lib/agent-page-context";

type Dict = Record<string, unknown>;
type SubjectAggregate = { subject?: Dict; state?: Dict };
type NextStep = { key: string; severity: string; title: string; detail: string; href: string; reviewItem?: Dict };

function asDict(value: unknown): Dict {
  return value && typeof value === "object" ? value as Dict : {};
}

function text(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function upper(value: unknown, fallback = "UNKNOWN"): string {
  return text(value, fallback).toUpperCase();
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

export default function DecisionWorkbenchPage() {
  const [requestedSubjectId, setRequestedSubjectId] = useState("");
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const workbenchApi = useApi<Dict>(
    requestedSubjectId
      ? `/api/decision-workbench?subject_id=${encodeURIComponent(requestedSubjectId)}`
      : "/api/decision-workbench",
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
  const retroUnavailable = partialFailures.includes("retro");
  const scorecardsUnavailable = partialFailures.includes("scorecards");
  const reviewItemsUnavailable = partialFailures.includes("review_items");
  const reviewItems = listOf<Dict>(workbenchApi.data, "review_items");

  useEffect(() => {
    if (activeSubjects.length === 0) {
      setRequestedSubjectId("");
      return;
    }
    if (requestedSubjectId && !activeSubjects.some(
      (item) => text(item.subject?.subject_id, "") === requestedSubjectId
    )) {
      setRequestedSubjectId("");
    }
  }, [activeSubjects, requestedSubjectId]);

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
  const instrumentId = text(plan?.instrument_id, text(subject.primary_instrument_id, ""));

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

  async function transitionReviewItem(item: Dict, status: "ACKNOWLEDGED" | "RESOLVED") {
    const reviewItemId = text(item.review_item_id, "");
    if (!reviewItemId) return;
    const resolutionNote = status === "RESOLVED" ? window.prompt("What durable fact or completed action closes this item?")?.trim() : undefined;
    if (status === "RESOLVED" && !resolutionNote) return;
    const dueDate = status === "ACKNOWLEDGED" && upper(item.status) === "ACKNOWLEDGED" ? window.prompt("Optional due date (YYYY-MM-DD):")?.trim() : undefined;
    const dueAt = dueDate ? new Date(`${dueDate}T23:59:59`).toISOString() : undefined;
    if (dueAt && Number.isNaN(Date.parse(dueAt))) { setReviewError("Due date must use YYYY-MM-DD."); return; }
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

  const loading = workbenchApi.loading;
  const error = workbenchApi.error;

  return <ConsoleShell active="decision-workbench">
    <DataBoundary loading={loading} error={error}>
      <div className="decision-workbench">
        <div className="toolbar decision-toolbar">
          <div><p>One Research Subject, one decision loop. This page aggregates durable state and sends edits back to the existing specialist workspaces.</p><small>No Provider refresh, automatic confirmation, position change, or order execution occurs here.</small></div>
          <div className="toolbar-actions"><label className="decision-subject-picker"><span>Research Subject</span><select value={subjectId} onChange={(event) => setRequestedSubjectId(event.target.value)}>{activeSubjects.map((item) => <option key={text(item.subject?.subject_id)} value={text(item.subject?.subject_id)}>{text(item.subject?.title)} · {shortId(item.subject?.primary_instrument_id)}</option>)}</select></label><RefreshButton loading={loading} onClick={workbenchApi.refresh} /></div>
        </div>

        {!selected ? <Empty>Create or activate a Research Subject in the existing Research workspace first.</Empty> : <>
          <section className="decision-subject-hero">
            <div><span>{upper(subject.subject_type, "RESEARCH SUBJECT")}</span><h2>{text(subject.title, "Unnamed Research Subject")}</h2><p>{text(subject.summary, "No stable research scope recorded.")}</p></div>
            <div className="decision-subject-meta"><Badge value={upper(subject.status)} /><strong>{shortId(subject.primary_instrument_id)}</strong><small className="mono">{subjectId}</small></div>
          </section>

          <Card className="decision-next-card" kicker="NEXT DECISION" title="Closure Queue" action={<div className="page-actions"><Badge value={partialFailures.length ? "INCOMPLETE" : nextSteps.length ? `${nextSteps.length} ITEMS` : "READY"} /><Link href="/#review-queue">All durable items →</Link></div>}><StepList items={nextSteps} busy={reviewBusy} onTransition={(item, status) => { void transitionReviewItem(item, status); }} />{reviewError ? <div className="inline-error">{reviewError}</div> : null}</Card>

          <div className="decision-stage-grid">
            <Card kicker="1 · JUDGMENT" title="Thesis & Questions" action={<Link href={`/research#subject-${subjectId}`}>Open Research →</Link>}>
              <div className="decision-stage-lead"><Badge value={researchUnavailable ? "UNAVAILABLE" : primaryThesis ? upper(primaryThesis.status) : "MISSING"} /><strong>{researchUnavailable ? "Research state read unavailable" : text(primaryThesis?.title, "No live Thesis")}</strong><p>{researchUnavailable ? "Retry this durable read before changing the judgment." : text(primaryRevision?.statement, "Create a falsifiable judgment before defining execution intent.")}</p></div>
              <div className="decision-metrics"><span>Live Theses<strong>{liveTheses.length}</strong></span><span>Open Questions<strong>{openQuestions.length}</strong></span><span>Pending Candidates<strong>{pendingCandidates.length}</strong></span></div>
            </Card>

            <Card kicker="2 · INTENT" title="Trade Plan" action={<Link href={`/research#subject-${subjectId}`}>Open plan →</Link>}>
              <div className="decision-stage-lead"><Badge value={researchUnavailable ? "UNAVAILABLE" : plan ? upper(plan.status) : "MISSING"} /><strong>{researchUnavailable ? "Trade Plan state read unavailable" : plan ? `${shortId(plan.instrument_id)} · v${text(plan.version, "—")}` : "No current Trade Plan"}</strong><p>{researchUnavailable ? "Retry the Research state read before interpreting plan coverage." : plan ? text(plan.notes, "Plan exists; inspect its exact conditions in Research.") : "A Trade Plan should encode conditional intent, never an automatic order."}</p></div>
              <div className="decision-metrics"><span>Conditions<strong>{listOf<Dict>(plan, "conditions").length}</strong></span><span>Target %<strong>{text(plan?.target_position_percent)}</strong></span><span>Max %<strong>{text(plan?.max_position_percent)}</strong></span></div>
            </Card>

            <Card kicker="3 · OBSERVE" title="Monitors" action={<Link href="/monitors">Open Monitors →</Link>}>
              <div className="decision-stage-lead"><Badge value={monitorsUnavailable ? "UNAVAILABLE" : activeMonitors.length ? "ACTIVE" : "UNCOVERED"} /><strong>{monitorsUnavailable ? "Monitor dashboard read unavailable" : `${linkedMonitors.length} linked · ${activeMonitors.length} active`}</strong><p>{monitorsUnavailable ? "Coverage is unknown; absence must not be interpreted as no Monitor." : latestMonitorRun ? `Latest run ${upper(latestMonitorRun.status)} · ${formatDate(latestMonitorRun.completed_at ?? latestMonitorRun.started_at)}` : "No linked Monitor run has been recorded."}</p></div>
              <div className="decision-metrics"><span>Triggered Rules<strong>{triggeredRules.length}</strong></span><span>Linked Monitors<strong>{linkedMonitors.length}</strong></span><span>Active<strong>{activeMonitors.length}</strong></span></div>
            </Card>

            <Card kicker="4 · EVENT LOOP" title="Catalyst Agenda" action={<Link href="/agenda#agenda-detail">Open Agenda →</Link>}>
              <div className="decision-stage-lead"><Badge value={agendaUnavailable ? "UNAVAILABLE" : overdueCatalysts.length ? "OVERDUE" : "TRACKING"} /><strong>{agendaUnavailable ? "Catalyst Agenda read unavailable" : `${upcomingCatalysts.length} upcoming · ${overdueCatalysts.length} overdue`}</strong><p>{agendaUnavailable ? "Upcoming and overdue counts are unknown until the durable read recovers." : upcomingCatalysts[0] ? `Next: ${text(upcomingCatalysts[0].title)} · ${formatDate(upcomingCatalysts[0].window_start)}` : "No upcoming durable Catalyst is in the selected window."}</p></div>
              <div className="decision-metrics"><span>Upcoming<strong>{upcomingCatalysts.length}</strong></span><span>Overdue<strong>{overdueCatalysts.length}</strong></span><span>Total Visible<strong>{catalysts.length}</strong></span></div>
            </Card>

            <Card kicker="5 · LEARN" title="Trade Retro" action={<Link href="/retro">Open Retro →</Link>}>
              <div className="decision-stage-lead"><Badge value={retroUnavailable ? "UNAVAILABLE" : latestRetro ? retroReviewStatus : "NO RUN"} /><strong>{retroUnavailable ? "Trade Retro history read unavailable" : latestRetro ? `${text(latestRetro.period_start).slice(0, 10)} → ${text(latestRetro.period_end).slice(0, 10)}` : "No related Trade Retro"}</strong><p>{retroUnavailable ? "Review status and findings are unknown until the durable read recovers." : latestRetro ? `${listOf<Dict>(latestRetro, "findings").length} deterministic finding(s) · generated ${formatDate(latestRetro.generated_at)}` : "Retro becomes attributable after a plan snapshot and durable broker transactions exist."}</p></div>
              <div className="decision-metrics"><span>Related Runs<strong>{relatedRetro.length}</strong></span><span>Findings<strong>{listOf<Dict>(latestRetro, "findings").length}</strong></span><span>Actions<strong>{listOf<string>(retroReview, "action_items").length}</strong></span></div>
            </Card>

            <Card kicker="6 · CALIBRATE" title="Judgment Scorecard" action={<Link href={`/scorecards?subject_id=${encodeURIComponent(subjectId)}`}>Open Scorecards →</Link>}>
              <div className="decision-stage-lead"><Badge value={scorecardsUnavailable ? "UNAVAILABLE" : latestScorecard ? upper(latestScorecard.status) : "NO RUN"} /><strong>{scorecardsUnavailable ? "Scorecard history read unavailable" : latestScorecard ? `Revision v${text(latestScorecard.thesis_revision_no)}` : "No immutable Scorecard"}</strong><p>{scorecardsUnavailable ? "Calibration gaps are unknown until the durable read recovers." : latestScorecard ? `${listOf<Dict>(latestScorecard, "dimensions").length} dimensions · ${scorecardGaps.length} gap(s) · ${formatDate(latestScorecard.generated_at)}` : "Generate a Scorecard from the specialist page after selecting an exact Thesis."}</p></div>
              <div className="decision-metrics"><span>Runs<strong>{cards.length}</strong></span><span>Dimensions<strong>{listOf<Dict>(latestScorecard, "dimensions").length}</strong></span><span>Gaps<strong>{scorecardGaps.length}</strong></span></div>
            </Card>
          </div>
        </>}
        {partialFailures.length ? <div id="decision-context-status" className="inline-error">Incomplete durable context: {partialFailures.join(", ")}. Available sections remain readable; retry before acting on the missing context.</div> : null}
      </div>
    </DataBoundary>
  </ConsoleShell>;
}
