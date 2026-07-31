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
import { monitorRunPresentation } from "./lib/monitor-runs";

type Dict = Record<string, unknown>;

export default function OverviewPage() {
  const result = useApi<Dict>("/api/overview");
  const health = envelopeData<Dict>(result.data?.health);
  const monitorData = envelopeData<Dict>(result.data?.monitor_dashboard);
  const monitorItems = listOf<Dict>(monitorData, "items");
  const runsData = envelopeData<Dict>(result.data?.recent_runs);
  const runs = listOf<Dict>(runsData, "runs");
  const triggered = monitorItems.flatMap((item) => listOf<Dict>(item, "rule_states"))
    .filter((rule) => rule.state === "TRIGGERED").length;
  const maintenance = result.data?.maintenance as Dict | undefined;
  const notifications = result.data?.notifications as Dict | undefined;
  const sync = result.data?.post_market_sync as Dict | undefined;
  const quality = health?.data_quality as Dict | undefined;
  const qualityIssues = listOf<Dict>(quality, "issues");
  const qualityAccounts = listOf<Dict>(quality, "account_snapshots");
  const qualityActivity = listOf<Dict>(quality, "account_activity");
  const qualityMonitors = listOf<Dict>(quality, "monitors");
  const blindMonitorCount = new Set(
    qualityIssues
      .filter((item) => item.scope === "monitor")
      .map((item) => String(item.subject_ref)),
  ).size;

  return (
    <ConsoleShell active="overview" eyebrow="System overview" title="投资研究控制台">
      <DataBoundary loading={result.loading} error={result.error}>
        <div className="summary-strip">
          <div>
            <span>系统</span>
            <strong>{String(health?.status ?? "—")}</strong>
          </div>
          <div>
            <span>公开能力</span>
            <strong>{String(result.data?.capability_count ?? 0)}</strong>
          </div>
          <div>
            <span>Active Monitor</span>
            <strong>{monitorItems.length}</strong>
          </div>
          <div>
            <span>触发规则</span>
            <strong className={triggered ? "text-amber" : ""}>{triggered}</strong>
          </div>
          <RefreshButton onClick={result.refresh} loading={result.loading} />
        </div>

        <div className="dashboard-grid">
          <Card
            className="span-12"
            kicker="DATA QUALITY"
            title="数据质量中心"
            action={<Badge value={String(quality?.status ?? "UNKNOWN")} />}
          >
            <div className="quality-center-grid">
              <div className="metric-pairs quality-metrics">
                <div><span>账户快照</span><strong>{qualityAccounts.length}</strong><small>持久化最新版本</small></div>
                <div><span>活动覆盖回执</span><strong>{qualityActivity.length}</strong><small>每账户最新回执</small></div>
                <div><span>Active Monitor</span><strong>{qualityMonitors.length}</strong><small>只读最近运行</small></div>
                <div><span>Monitor 盲区</span><strong className={blindMonitorCount ? "text-amber" : ""}>{blindMonitorCount}</strong><small>未运行 / 未评估 / 不完整</small></div>
              </div>
              <div className="quality-issues">
                <div className="quality-section-heading">
                  <span>当前缺口</span>
                  <small>{qualityIssues.length} 项 · 不触发上游请求</small>
                </div>
                {qualityIssues.length === 0 ? (
                  <Empty>持久化证据未发现质量缺口。</Empty>
                ) : (
                  <div className="quality-issue-list">
                    {qualityIssues.slice(0, 6).map((issue, index) => (
                      <article key={`${String(issue.code)}-${String(issue.subject_ref)}-${index}`}>
                        <Badge value={String(issue.severity ?? "DEGRADED")} />
                        <div>
                          <strong>{String(issue.code)}</strong>
                          <span>{String(issue.subject_ref ?? issue.scope ?? "system")} · {String(issue.detail ?? "—")}</span>
                        </div>
                      </article>
                    ))}
                    {qualityIssues.length > 6 ? <small>另有 {qualityIssues.length - 6} 项，可通过 system_health 读取完整机器视图。</small> : null}
                  </div>
                )}
              </div>
            </div>
          </Card>

          <Card
            className="span-8"
            kicker="MONITOR PULSE"
            title="当前监控态势"
            action={<Link className="text-link" href="/monitors">查看全部 →</Link>}
          >
            {monitorItems.length === 0 ? (
              <Empty>尚无 Monitor 定义。</Empty>
            ) : (
              <div className="monitor-overview-list">
                {monitorItems.slice(0, 4).map((item) => {
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
                          {String(monitor.name ?? "未命名 Monitor")}
                        </Link>
                        <span>创建 {formatDate(item.monitor_created_at ?? monitor.created_at)} · {String(monitor.cadence ?? "—")} · v{String(monitor.version ?? "—")}</span>
                      </div>
                      <div className="state-dots" aria-label="规则状态">
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

          <Card className="span-4" kicker="AUTOMATION" title="收盘后运行">
            <div className="status-hero">
              <Badge value={String(sync?.health ?? "UNKNOWN")} />
              <strong>{String(sync?.run_status ?? "无回执")}</strong>
              <span>最近会话 {String(sync?.receipt_session_date ?? "—")}</span>
            </div>
            <dl className="detail-list">
              <div><dt>账户同步</dt><dd>{String(sync?.portfolio_status ?? "—")}</dd></div>
              <div><dt>自选同步</dt><dd>{String(sync?.watchlist_status ?? "—")}</dd></div>
              <div><dt>OAuth</dt><dd><Badge value={String((sync?.schwab_oauth as Dict | undefined)?.state ?? "—")} /></dd></div>
            </dl>
          </Card>

          <Card className="span-7" kicker="RECENT RUNS" title="最近 Monitor Run">
            {runs.length === 0 ? <Empty>尚无运行记录。</Empty> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>标的 / Monitor</th><th>完成时间</th><th>Cadence</th><th>规则</th><th>事件</th><th>状态</th></tr></thead>
                  <tbody>
                    {runs.map((run) => {
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

          <Card className="span-5" kicker="OPERATIONS" title="本地运行面">
            <div className="metric-pairs">
              <div><span>数据库</span><strong>{formatBytes(maintenance?.database_bytes)}</strong><small>{String(maintenance?.database_filename ?? "—")}</small></div>
              <div><span>备份</span><strong>{String(maintenance?.backup_files ?? 0)}</strong><small>最近 {formatDate(maintenance?.latest_backup_at)}</small></div>
              <div><span>通知待发</span><strong>{String(notifications?.pending ?? 0)}</strong><small>{String(notifications?.provider ?? "未配置")}</small></div>
              <div><span>缓存已过期</span><strong>{String(maintenance?.provider_cache_expired ?? 0)}</strong><small>显式命令清理</small></div>
            </div>
          </Card>
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
