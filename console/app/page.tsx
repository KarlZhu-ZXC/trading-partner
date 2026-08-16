"use client";

import Link from "next/link";
import { useState } from "react";
import { ConsoleShell } from "./components/console-shell";
import {
  ActionButton,
  Badge,
  Card,
  DataBoundary,
  Empty,
  RefreshButton,
  formatBytes,
  formatDate,
  monitorAnchorId,
  shortId,
} from "./components/ui";
import { envelopeData, listOf, postApi, useApi } from "./lib/api";
import { buildConsoleNotices } from "./lib/attention";
import { monitorRunPresentation } from "./lib/monitor-runs";

type Dict = Record<string, unknown>;

function agendaSummary(payload: unknown) {
  const source = envelopeData<Dict>(payload);
  const items = listOf<Dict>(source, "items");
  const coverage = listOf<Dict>(source, "coverage");
  const now = Date.now();
  const sevenDays = now + 7 * 24 * 60 * 60 * 1000;
  const isOverdue = (item: Dict) => Array.isArray(item.limitation_codes)
    && item.limitation_codes.includes("AGENDA_OUTCOME_UNVERIFIED");
  const upcoming = items.filter((item) => String(item.status) === "UPCOMING" && !isOverdue(item));
  return {
    upcoming7d: upcoming.filter((item) => {
      const startsAt = Date.parse(String(item.window_start ?? ""));
      return Number.isFinite(startsAt) && startsAt <= sevenDays;
    }).length,
    upcoming: upcoming.length,
    overdue: items.filter(isOverdue).length,
    coverageGap: coverage.filter((item) => String(item.status) === "UNAVAILABLE").length,
  };
}

function durationLabel(value: unknown): string {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

export default function OverviewPage() {
  const result = useApi<Dict>("/api/overview");
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const health = envelopeData<Dict>(result.data?.health);
  const monitorData = envelopeData<Dict>(result.data?.monitor_dashboard);
  const monitorItems = listOf<Dict>(monitorData, "items");
  const activeMonitorItems = monitorItems.filter((item) => {
    const monitor = (item.monitor ?? {}) as Dict;
    return String(monitor.status ?? "").toUpperCase() === "ACTIVE";
  });
  const runsData = envelopeData<Dict>(result.data?.recent_runs);
  const runs = listOf<Dict>(runsData, "runs");
  const triggered = activeMonitorItems.flatMap((item) => listOf<Dict>(item, "rule_states"))
    .filter((rule) => rule.state === "TRIGGERED").length;
  const maintenance = result.data?.maintenance as Dict | undefined;
  const notifications = result.data?.notifications as Dict | undefined;
  const sync = result.data?.post_market_sync as Dict | undefined;
  const quality = health?.data_quality as Dict | undefined;
  const qualityIssues = listOf<Dict>(quality, "issues");
  const qualityAccounts = listOf<Dict>(quality, "account_snapshots");
  const qualityActivity = listOf<Dict>(quality, "account_activity");
  const qualityMonitors = listOf<Dict>(quality, "monitors");
  const qualityRoutes = listOf<Dict>(quality, "provider_routes");
  const recentFallbacks = qualityRoutes.reduce(
    (total, item) => total + Number(item.fallback_count ?? 0),
    0,
  );
  const recentRouteFailures = qualityRoutes.reduce(
    (total, item) => total + Number(item.failure_count ?? 0),
    0,
  );
  const blindMonitorCount = new Set(
    qualityIssues
      .filter((item) => item.scope === "monitor")
      .map((item) => String(item.subject_ref)),
  ).size;
  const researchAttention = listOf<Dict>(result.data, "research_attention");
  const workflowAttention = listOf<Dict>(result.data, "workflow_attention");
  const reviewItems = listOf<Dict>(result.data, "review_items");
  const reviewMetrics = (result.data?.review_item_metrics ?? {}) as Dict;
  const unresolvedReviewCount = Number(reviewMetrics.open_count ?? 0)
    + Number(reviewMetrics.acknowledged_count ?? 0);
  const closedReviewItems = listOf<Dict>(result.data, "review_item_history").filter((item) =>
    ["RESOLVED", "AUTO_RESOLVED"].includes(String(item.status))
  );
  const notices = buildConsoleNotices({
    monitorItems,
    runs,
    researchAttention,
    workflowAttention,
    notifications,
    qualityIssues,
    qualityAccounts,
    qualityActivity,
    qualityRoutes,
  });
  const agendaCounts = agendaSummary(result.data?.agenda_summary);

  async function transitionReviewItem(item: Dict, status: "ACKNOWLEDGED" | "RESOLVED") {
    const reviewItemId = String(item.review_item_id ?? "");
    if (!reviewItemId) return;
    const resolutionNote = status === "RESOLVED"
      ? window.prompt("What durable fact or completed action closes this review item?")?.trim()
      : undefined;
    if (status === "RESOLVED" && !resolutionNote) return;
    const dueDate = status === "ACKNOWLEDGED" && String(item.status) === "ACKNOWLEDGED"
      ? window.prompt("Optional due date (YYYY-MM-DD):")?.trim()
      : undefined;
    const dueAt = dueDate ? new Date(`${dueDate}T23:59:59`).toISOString() : undefined;
    if (dueAt && Number.isNaN(Date.parse(dueAt))) {
      setReviewError("Due date must use YYYY-MM-DD.");
      return;
    }
    setReviewBusy(reviewItemId);
    setReviewError(null);
    try {
      await postApi<Dict>(`/api/review-items/${encodeURIComponent(reviewItemId)}/transition`, {
        status,
        expected_version: Number(item.version),
        resolution_note: resolutionNote,
        due_at: dueAt,
        idempotency_key: `console-review-${reviewItemId}-${status.toLowerCase()}-${crypto.randomUUID()}`,
        authorization_note: `User explicitly selected ${status.toLowerCase()} in the Console Decision Inbox.`,
        confirmation: "review_item_update",
      });
      result.refresh();
    } catch (cause) {
      setReviewError(cause instanceof Error ? cause.message : "Review item update failed");
    } finally {
      setReviewBusy(null);
    }
  }

  return (
    <ConsoleShell active="overview">
      <DataBoundary loading={result.loading} error={result.error}>
        <Card className="span-12" kicker="EVENT COVERAGE" title="Catalyst Pulse" subtitle="Upcoming schedule and unresolved timing gaps" action={<Link href="/agenda">Open /agenda</Link>}>
          <div className="agenda-summary-grid">
            <div><span>Upcoming 7 Days</span><strong>{String(agendaCounts.upcoming7d)}</strong></div>
            <div><span>Upcoming</span><strong>{String(agendaCounts.upcoming)}</strong></div>
            <div><span>Overdue</span><strong>{String(agendaCounts.overdue)}</strong></div>
            <div><span>Coverage Gap</span><strong>{String(agendaCounts.coverageGap)}</strong></div>
          </div>
        </Card>

        <div className="summary-strip">
          <div>
            <span>System</span>
            <strong>{String(health?.status ?? "—")}</strong>
          </div>
          <div>
            <span>Public Tools</span>
            <strong>{String(result.data?.capability_count ?? 0)}</strong>
          </div>
          <div>
            <span>Active Monitor</span>
            <strong>{activeMonitorItems.length}</strong>
          </div>
          <div>
            <span>Triggered Rules</span>
            <strong className={triggered ? "text-amber" : ""}>{triggered}</strong>
          </div>
          <RefreshButton onClick={result.refresh} loading={result.loading} />
        </div>

        <div className="dashboard-grid">
          <Card className="span-12" kicker="DECISION WORKFLOW" title="Today’s Inbox" subtitle="Manual actions that need a deliberate response" action={<Badge value={`${notices.actionItems.length} ACTIONS`} />}>
            {notices.actionItems.length === 0 ? <div className="attention-clear"><span aria-hidden="true">✓</span><div><strong>No Manual Action Required</strong><small>Operational constraints and automatic retries appear separately below and do not count as Attention.</small></div></div> : <div className="attention-queue">{notices.actionItems.slice(0, 16).map((item) => <Link href={item.href} key={item.key}><Badge value={item.severity} /><div><strong>{item.title}</strong><span>{item.detail}</span></div><span aria-hidden="true">→</span></Link>)}</div>}
            {notices.automaticItems.length > 0 ? (
              <div className="automatic-recovery">
                <div className="quality-section-heading"><span>Waiting for Next Evaluation</span><small>Not the Current Source Status</small></div>
                <div className="attention-queue">{notices.automaticItems.slice(0, 6).map((item) => <Link href={item.href} key={item.key}><Badge value={item.severity} /><div><strong>{item.title}</strong><span>{item.detail}</span></div><span aria-hidden="true">→</span></Link>)}</div>
              </div>
            ) : null}
          </Card>
          <Card id="review-queue" className="span-12" kicker="DURABLE CLOSURE" title="Review Queue" subtitle="Items awaiting acknowledgment, scheduling, or resolution" action={<Badge value={`${unresolvedReviewCount} UNRESOLVED`} />}>
            <div className="review-metrics" aria-label="Review Queue Lifecycle Metrics">
              <span>Open<strong>{String(Number(reviewMetrics.open_count ?? 0) + Number(reviewMetrics.acknowledged_count ?? 0))}</strong></span>
              <span>Overdue<strong>{String(reviewMetrics.overdue_count ?? 0)}</strong></span>
              <span>Oldest Current Gap<strong>{durationLabel(reviewMetrics.oldest_current_open_age_seconds)}</strong></span>
              <span>Median Acknowledge<strong>{durationLabel(reviewMetrics.median_open_to_ack_seconds)}</strong><small>n={String(reviewMetrics.acknowledgment_sample_size ?? 0)}</small></span>
              <span>Median Close<strong>{durationLabel(reviewMetrics.median_open_to_close_seconds)}</strong><small>n={String(reviewMetrics.closure_sample_size ?? 0)}</small></span>
              <span>Recurring<strong>{String(reviewMetrics.recurring_count ?? 0)}</strong></span>
            </div>
            {reviewItems.length === 0 ? <div className="attention-clear"><span aria-hidden="true">✓</span><div><strong>No Unresolved ReviewItems</strong><small>Recovered source conditions are closed automatically; human resolutions retain their receipt.</small></div></div> : <div className="review-item-list">{reviewItems.slice(0, 20).map((item) => <article className="review-item-row" key={String(item.review_item_id)}><Link href={String(item.href ?? "/")}><Badge value={String(item.severity ?? "ATTENTION")} /><div><strong>{String(item.title ?? "Review required")}</strong><span>{String(item.detail ?? "Inspect the durable source.")}</span><small>{String(item.status)} · seen {formatDate(item.first_seen_at)} → {formatDate(item.last_seen_at)} · occurrence {String(item.occurrence_count)}{item.due_at ? ` · due ${formatDate(item.due_at)}` : ""}</small></div></Link><div className="review-item-actions"><ActionButton busy={reviewBusy === item.review_item_id} onClick={() => { void transitionReviewItem(item, "ACKNOWLEDGED"); }}>{String(item.status) === "ACKNOWLEDGED" ? "Update Due" : "Acknowledge"}</ActionButton><ActionButton busy={reviewBusy === item.review_item_id} tone="warning" onClick={() => { void transitionReviewItem(item, "RESOLVED"); }}>Resolve</ActionButton></div></article>)}</div>}
            {reviewError ? <div className="inline-error">{reviewError}</div> : null}
            {closedReviewItems.length > 0 ? <details className="review-item-history"><summary>Recently Closed · {closedReviewItems.length}</summary><div>{closedReviewItems.slice(0, 12).map((item) => <article key={String(item.review_item_id)}><div><Badge value={String(item.status)} /><strong>{String(item.title)}</strong></div><span>{String(item.resolution_note ?? "The durable source no longer reports this issue.")}</span><small>{formatDate(item.resolved_at)} · occurrence {String(item.occurrence_count)}{item.resolution_ref ? ` · ${String(item.resolution_ref)}` : ""}</small></article>)}</div></details> : null}
          </Card>
          <Card
            className="span-12"
            kicker="DATA QUALITY"
            title="Data Quality Center"
            subtitle="Freshness and completeness of persisted facts"
            action={<Badge value={String(quality?.status ?? "UNKNOWN")} />}
          >
            <div className="quality-center-grid">
              <div className="metric-pairs quality-metrics">
                <div><span>Account Snapshots</span><strong>{qualityAccounts.length}</strong><small>Latest Durable Versions</small></div>
                <div><span>Activity Coverage</span><strong>{qualityActivity.length}</strong><small>Latest Receipt per Account</small></div>
                <div><span>Active Monitors</span><strong>{qualityMonitors.length}</strong><small>Latest Runs, Read Only</small></div>
                <div><span>Monitor Blind Spots</span><strong className={blindMonitorCount ? "text-amber" : ""}>{blindMonitorCount}</strong><small>Not Run / Unevaluated / Incomplete</small></div>
                <div><span>24h Provider Fallbacks</span><strong className={recentFallbacks ? "text-amber" : ""}>{recentFallbacks}</strong><small>{qualityRoutes.length} market/category pairs</small></div>
                <div><span>24h Provider Failures</span><strong className={recentRouteFailures ? "text-amber" : ""}>{recentRouteFailures}</strong><small>Secret-Safe Route Receipts</small></div>
              </div>
              <div className="quality-issues">
                <div className="quality-section-heading">
                  <span>Current Gaps</span>
                  <small>{notices.qualityItems.length} Groups · No Upstream Requests</small>
                </div>
                {notices.qualityItems.length === 0 ? (
                  <Empty>No quality gaps found in durable evidence.</Empty>
                ) : (
                  <div className="quality-issue-list">
                    {notices.qualityItems.slice(0, 6).map((item) => (
                      <Link className="quality-issue-link" href={item.href} key={item.key}>
                      <article>
                        <Badge value={item.severity} />
                        <div>
                          <strong>{item.title}</strong>
                          <span>{item.detail}</span>
                        </div>
                      </article>
                      </Link>
                    ))}
                    {notices.qualityItems.length > 6 ? <small>{notices.qualityItems.length - 6} more groups are available in the full system_health machine view.</small> : null}
                  </div>
                )}
              </div>
            </div>
          </Card>

          <Card
            className="span-8"
            kicker="MONITOR PULSE"
            title="Current Monitor Posture"
            subtitle="Latest rule state from each active definition"
            action={<Link className="text-link" href="/monitors">View All →</Link>}
          >
            {activeMonitorItems.length === 0 ? (
              <Empty>No Monitor definitions yet.</Empty>
            ) : (
              <div className="monitor-overview-list">
                {activeMonitorItems.slice(0, 4).map((item) => {
                  const monitor = (item.monitor ?? {}) as Dict;
                  const states = listOf<Dict>(item, "rule_states");
                  const run = (item.latest_run ?? {}) as Dict;
                  return (
                    <article className="monitor-row" key={String(monitor.monitor_id)}>
                      <div className="symbol-tile">{shortId(monitor.primary_instrument_id)}</div>
                      <div className="monitor-copy">
                        <Link
                          className="monitor-title-link"
                          href={`/monitors#${monitorAnchorId(monitor.monitor_id)}`}
                        >
                          {String(monitor.name ?? "Untitled Monitor")}
                        </Link>
                        <span>Created {formatDate(item.monitor_created_at ?? monitor.created_at)} · {String(monitor.cadence ?? "—")} · v{String(monitor.version ?? "—")}</span>
                      </div>
                      <div className="state-dots" aria-label="Rule States">
                        {states.slice(0, 10).map((state) => (
                          <i
                            className={`state-dot ${String(state.state ?? "").toLowerCase()}`}
                            key={String(state.rule_code)}
                            title={`${String(state.rule_code)}: ${String(state.state)}`}
                          />
                        ))}
                      </div>
                      <div className="run-meta">
                        <Badge value={String(run.status ?? monitor.status ?? "—")} />
                        <span>{formatDate(run.completed_at)}</span>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </Card>

          <Card className="span-4" kicker="AUTOMATION" title="Post-Market Run" subtitle="Latest scheduled account and Watchlist refresh">
            <div className="status-hero">
              <Badge value={String(sync?.health ?? "UNKNOWN")} />
              <strong>{String(sync?.run_status ?? "No receipt")}</strong>
              <span>Latest Session {String(sync?.receipt_session_date ?? "—")}</span>
            </div>
            <dl className="detail-list">
              <div><dt>Account Sync</dt><dd>{String(sync?.portfolio_status ?? "—")}</dd></div>
              <div><dt>Watchlist Sync</dt><dd>{String(sync?.watchlist_status ?? "—")}</dd></div>
              <div><dt>OAuth</dt><dd><Badge value={String((sync?.schwab_oauth as Dict | undefined)?.state ?? "—")} /></dd></div>
            </dl>
          </Card>

          <Card className="span-7" kicker="OBSERVATION HISTORY" title="Recent Monitor Runs" subtitle="Latest immutable evaluation batches">
            {runs.length === 0 ? <Empty>No run history yet.</Empty> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Target / Monitor</th><th>Completed</th><th>Cadence</th><th>Rules</th><th>Events</th><th>Status</th></tr></thead>
                  <tbody>
                    {runs.slice(0, 8).map((run) => {
                      const identity = monitorRunPresentation(run, monitorItems);
                      return (
                        <tr key={String(run.run_id)}>
                          <td><div className="run-target-cell"><strong>{identity.symbolLabel}</strong><small>{identity.nameLabel}</small></div></td>
                          <td>{formatDate(run.completed_at)}</td>
                          <td className="mono">{String(run.cadence ?? "MANUAL")}</td>
                          <td>{String(run.rules_evaluated ?? 0)}</td>
                          <td>{String(run.events_created ?? 0)}</td>
                          <td><Badge value={String(run.status ?? "—")} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card className="span-5" kicker="OPERATIONS" title="Local Runtime" subtitle="Database, backups, and notification delivery">
            <div className="metric-pairs">
              <div><span>Database</span><strong>{formatBytes(maintenance?.database_bytes)}</strong><small>{String(maintenance?.database_filename ?? "—")}</small></div>
              <div><span>Backups</span><strong>{String(maintenance?.backup_files ?? 0)}</strong><small>Latest {formatDate(maintenance?.latest_backup_at)}</small></div>
              <div><span>Notifications Pending</span><strong>{String(notifications?.pending ?? 0)}</strong><small>{String(notifications?.provider ?? "Not configured")}</small></div>
              <div><span>Expired Cache</span><strong>{String(maintenance?.provider_cache_expired ?? 0)}</strong><small>Removed only by explicit command</small></div>
            </div>
          </Card>
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
