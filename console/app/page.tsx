"use client";

import Link from "next/link";
import { ConsoleShell } from "./components/console-shell";
import {
  Badge,
  Card,
  DataBoundary,
  QuickLink,
  Empty,
  MetricTile,
  RefreshButton,
  formatBytes,
  formatDate,
  monitorAnchorId,
  shortId,
} from "./components/ui";
import { envelopeData, listOf, useApi } from "./lib/api";
import { buildConsoleNotices } from "./lib/attention";
import { agendaSummaryFromPayload } from "./lib/agenda-presentation";
import { monitorRunPresentation } from "./lib/monitor-runs";

type Dict = Record<string, unknown>;

function durationLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

export default function OverviewPage() {
  const result = useApi<Dict>("/api/overview");
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
  const reviewMetrics = (result.data?.review_item_metrics ?? {}) as Dict;
  const unresolvedReviewCount = Number(reviewMetrics.open_count ?? 0)
    + Number(reviewMetrics.acknowledged_count ?? 0);
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
  const groupedInboxCount = notices.actionItems.length + (unresolvedReviewCount > 0 ? 1 : 0);
  const agendaCounts = agendaSummaryFromPayload(result.data?.agenda_summary);

  return (
    <ConsoleShell active="overview">
      <DataBoundary loading={result.loading} error={result.error}>
        <Card className="span-12" kicker="EVENT COVERAGE" title="Catalyst Pulse" subtitle="Upcoming schedule and unresolved timing gaps" action={<QuickLink href="/agenda">Open /agenda</QuickLink>}>
          <div className="agenda-summary-grid">
            <MetricTile label="Upcoming 7 Days" value={String(agendaCounts.upcoming7d)} />
            <MetricTile label="Upcoming" value={String(agendaCounts.upcoming)} />
            <MetricTile label="Overdue" value={String(agendaCounts.overdue)} />
            <MetricTile label="Coverage Gap" value={String(agendaCounts.coverageGap)} />
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
          <Card id="review-queue" className="span-12" kicker="DECISION WORKFLOW" title="Action & Review Inbox" subtitle="Grouped manual actions and durable closure metrics" action={<div className="page-actions"><Badge value={`${groupedInboxCount} GROUPS`} /><QuickLink href="/decision-workbench#reviews">Open Reviews</QuickLink></div>}>
            {groupedInboxCount === 0 ? <div className="attention-clear"><span aria-hidden="true">✓</span><div><strong>No Manual Action Required</strong><small>Operational constraints and automatic retries appear separately below and do not count as Attention.</small></div></div> : <div className="attention-queue">{unresolvedReviewCount > 0 ? <Link href="/decision-workbench#reviews"><Badge value="REVIEW" /><div><strong>Review Queue</strong><span>{unresolvedReviewCount} open or acknowledged items · grouped here to avoid flooding the Home page.</span></div><span aria-hidden="true">→</span></Link> : null}{notices.actionItems.slice(0, 15).map((item) => <Link href={item.href} key={item.key}><Badge value={item.severity} /><div><strong>{item.title}</strong><span>{item.detail}</span></div><span aria-hidden="true">→</span></Link>)}</div>}
            {notices.automaticItems.length > 0 ? (
              <div className="automatic-recovery">
                <div className="quality-section-heading"><span>Waiting for Next Evaluation</span><small>Not the Current Source Status</small></div>
                <div className="attention-queue">{notices.automaticItems.slice(0, 6).map((item) => <Link href={item.href} key={item.key}><Badge value={item.severity} /><div><strong>{item.title}</strong><span>{item.detail}</span></div><span aria-hidden="true">→</span></Link>)}</div>
              </div>
            ) : null}
            <div className="review-metrics" aria-label="Review Queue Lifecycle Metrics">
              <span>Open<strong>{String(Number(reviewMetrics.open_count ?? 0) + Number(reviewMetrics.acknowledged_count ?? 0))}</strong></span>
              <span>Overdue<strong>{String(reviewMetrics.overdue_count ?? 0)}</strong></span>
              <span>Oldest Current Gap<strong>{durationLabel(reviewMetrics.oldest_current_open_age_seconds)}</strong></span>
              <span>Median Acknowledge<strong>{durationLabel(reviewMetrics.median_open_to_ack_seconds)}</strong><small>n={String(reviewMetrics.acknowledgment_sample_size ?? 0)}</small></span>
              <span>Median Close<strong>{durationLabel(reviewMetrics.median_open_to_close_seconds)}</strong><small>n={String(reviewMetrics.closure_sample_size ?? 0)}</small></span>
              <span>Recurring<strong>{String(reviewMetrics.recurring_count ?? 0)}</strong></span>
            </div>
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
                <MetricTile label="Account Snapshots" value={qualityAccounts.length} detail="Latest Durable Versions" />
                <MetricTile label="Activity Coverage" value={qualityActivity.length} detail="Latest Receipt per Account" />
                <MetricTile label="Active Monitors" value={qualityMonitors.length} detail="Latest Runs, Read Only" />
                <MetricTile label="Monitor Blind Spots" value={blindMonitorCount} valueClassName={blindMonitorCount ? "text-amber" : ""} detail="Not Run / Unevaluated / Incomplete" />
                <MetricTile label="24H Provider Fallbacks" value={recentFallbacks} valueClassName={recentFallbacks ? "text-amber" : ""} detail={`${qualityRoutes.length} market/category pairs`} />
                <MetricTile label="24H Provider Failures" value={recentRouteFailures} valueClassName={recentRouteFailures ? "text-amber" : ""} detail="Secret-Safe Route Receipts" />
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
            subtitle="Triggered and unavailable rules first; quiet rules are summarized"
            action={<QuickLink href="/monitors">View All</QuickLink>}
          >
            {activeMonitorItems.length === 0 ? (
              <Empty>No Monitor definitions yet.</Empty>
            ) : (
              <div className="monitor-overview-list">
                {activeMonitorItems.slice(0, 4).map((item) => {
                  const monitor = (item.monitor ?? {}) as Dict;
                  const states = listOf<Dict>(item, "rule_states");
                  const attentionStates = states.filter((state) => String(state.state ?? "").toUpperCase() !== "QUIET");
                  const triggeredStates = attentionStates.filter((state) => String(state.state ?? "").toUpperCase() === "TRIGGERED");
                  const unavailableStates = attentionStates.length - triggeredStates.length;
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
                      <div className="monitor-state-summary" aria-label="Rule State Summary">
                        <strong>{triggeredStates.length} Triggered</strong>
                        <span>{unavailableStates ? `${unavailableStates} Unavailable · ` : ""}{states.length - attentionStates.length} Quiet</span>
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
              <MetricTile label="Database" value={formatBytes(maintenance?.database_bytes)} detail={String(maintenance?.database_filename ?? "—")} />
              <MetricTile label="Backups" value={String(maintenance?.backup_files ?? 0)} detail={<>Latest {formatDate(maintenance?.latest_backup_at)}</>} />
              <MetricTile label="Notifications Pending" value={String(notifications?.pending ?? 0)} detail={String(notifications?.provider ?? "Not configured")} />
              <MetricTile label="Expired Cache" value={String(maintenance?.provider_cache_expired ?? 0)} detail="Removed only by explicit command" />
            </div>
          </Card>
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
