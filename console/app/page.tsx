"use client";

import Link from "next/link";
import { ConsoleShell } from "./components/console-shell";
import {
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
import { envelopeData, listOf, useApi } from "./lib/api";
import { buildConsoleNotices } from "./lib/attention";
import { monitorRunPresentation } from "./lib/monitor-runs";

type Dict = Record<string, unknown>;

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
  const notices = buildConsoleNotices({
    monitorItems,
    runs,
    researchAttention,
    notifications,
    qualityIssues,
    qualityAccounts,
    qualityActivity,
    qualityRoutes,
  });

  return (
    <ConsoleShell active="overview" eyebrow="System overview" title="Investment Research Console">
      <DataBoundary loading={result.loading} error={result.error}>
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
          <Card className="span-12" kicker="ATTENTION QUEUE" title="Needs Attention" action={<Badge value={`${notices.actionItems.length} ACTIONS`} />}>
            {notices.actionItems.length === 0 ? <div className="attention-clear"><span aria-hidden="true">✓</span><div><strong>No manual action required</strong><small>Operational constraints and automatic retries appear separately below and do not count as Attention.</small></div></div> : <div className="attention-queue">{notices.actionItems.slice(0, 16).map((item) => <Link href={item.href} key={item.key}><Badge value={item.severity} /><div><strong>{item.title}</strong><span>{item.detail}</span></div><span aria-hidden="true">→</span></Link>)}</div>}
            {notices.automaticItems.length > 0 ? (
              <div className="automatic-recovery">
                <div className="quality-section-heading"><span>Waiting for Next Evaluation</span><small>Not the current source status</small></div>
                <div className="attention-queue">{notices.automaticItems.slice(0, 6).map((item) => <Link href={item.href} key={item.key}><Badge value={item.severity} /><div><strong>{item.title}</strong><span>{item.detail}</span></div><span aria-hidden="true">→</span></Link>)}</div>
              </div>
            ) : null}
          </Card>
          <Card
            className="span-12"
            kicker="DATA QUALITY"
            title="Data Quality Center"
            action={<Badge value={String(quality?.status ?? "UNKNOWN")} />}
          >
            <div className="quality-center-grid">
              <div className="metric-pairs quality-metrics">
                <div><span>Account Snapshots</span><strong>{qualityAccounts.length}</strong><small>Latest durable versions</small></div>
                <div><span>Activity Coverage</span><strong>{qualityActivity.length}</strong><small>Latest receipt per account</small></div>
                <div><span>Active Monitors</span><strong>{qualityMonitors.length}</strong><small>Latest runs, read only</small></div>
                <div><span>Monitor Blind Spots</span><strong className={blindMonitorCount ? "text-amber" : ""}>{blindMonitorCount}</strong><small>Not run / unevaluated / incomplete</small></div>
                <div><span>24h Provider Fallbacks</span><strong className={recentFallbacks ? "text-amber" : ""}>{recentFallbacks}</strong><small>{qualityRoutes.length} market/category pairs</small></div>
                <div><span>24h Provider Failures</span><strong className={recentRouteFailures ? "text-amber" : ""}>{recentRouteFailures}</strong><small>Secret-safe route receipts</small></div>
              </div>
              <div className="quality-issues">
                <div className="quality-section-heading">
                  <span>Current Gaps</span>
                  <small>{notices.qualityItems.length} groups · no upstream requests</small>
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
            action={<Link className="text-link" href="/monitors">View all →</Link>}
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
                      <div className="state-dots" aria-label="Rule states">
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

          <Card className="span-4" kicker="AUTOMATION" title="Post-Market Run">
            <div className="status-hero">
              <Badge value={String(sync?.health ?? "UNKNOWN")} />
              <strong>{String(sync?.run_status ?? "No receipt")}</strong>
              <span>Latest session {String(sync?.receipt_session_date ?? "—")}</span>
            </div>
            <dl className="detail-list">
              <div><dt>Account Sync</dt><dd>{String(sync?.portfolio_status ?? "—")}</dd></div>
              <div><dt>Watchlist Sync</dt><dd>{String(sync?.watchlist_status ?? "—")}</dd></div>
              <div><dt>OAuth</dt><dd><Badge value={String((sync?.schwab_oauth as Dict | undefined)?.state ?? "—")} /></dd></div>
            </dl>
          </Card>

          <Card className="span-7" kicker="RECENT RUNS" title="Recent Monitor Runs">
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

          <Card className="span-5" kicker="OPERATIONS" title="Local Runtime">
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
