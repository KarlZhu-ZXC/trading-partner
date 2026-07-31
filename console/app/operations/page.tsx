"use client";

import { useEffect, useState } from "react";
import { ConsoleShell } from "../components/console-shell";
import { ActionButton, Badge, Card, DataBoundary, RefreshButton, displayJson, formatBytes, formatDate } from "../components/ui";
import { listOf, postApi, useApi } from "../lib/api";

type Dict = Record<string, unknown>;

export default function OperationsPage() {
  const result = useApi<Dict>("/api/operations");
  const oauthResult = useApi<Dict>("/api/schwab/oauth");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<unknown>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const sync = result.data?.post_market_sync as Dict | undefined;
  const oauthFlow = oauthResult.data?.flow as Dict | undefined;
  const oauth = (oauthResult.data?.token_health as Dict | undefined)
    ?? (sync?.schwab_oauth as Dict | undefined);
  const oauthFlowState = String(oauthFlow?.state ?? "IDLE");
  const oauthConfigured = oauthResult.data?.configured !== false;
  const oauthRetryRequiresConfirmation = oauthFlow?.retry_requires_confirmation === true;
  const notification = result.data?.notifications as Dict | undefined;
  const maintenance = result.data?.maintenance as Dict | undefined;
  const tableCounts = listOf<Dict>(maintenance, "table_counts");
  const retention = listOf<Dict>(maintenance, "retention_rules");

  useEffect(() => {
    if (oauthFlowState !== "ACTIVE") return;
    const timer = window.setInterval(oauthResult.refresh, 1000);
    return () => window.clearInterval(timer);
  }, [oauthFlowState, oauthResult.refresh]);

  async function runAction(action: string, warning?: string) {
    if (warning && !window.confirm(warning)) return;
    setBusy(action);
    setActionError(null);
    try {
      const value = await postApi<unknown>("/api/actions/run", {
        action,
        confirmation: action,
        retention_days: 30,
      });
      setActionResult(value);
      result.refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "操作失败");
    } finally {
      setBusy(null);
    }
  }

  async function startSchwabReauthorization(confirmRetryAfterFailure = false) {
    const warning = confirmRetryAfterFailure
      ? "请确认旧的 Schwab 授权标签页已经关闭。系统将创建一个新的 OAuth state 并打开一个新标签页。继续？"
      : "项目将打开一个新的 Schwab 授权标签页，并等待最多五分钟接收本地回调。请只操作这次新打开的标签页。继续？";
    if (!window.confirm(warning)) return;
    const action = confirmRetryAfterFailure
      ? "schwab_oauth_renew_confirmed"
      : "schwab_oauth_renew";
    setBusy(action);
    setActionError(null);
    try {
      const value = await postApi<unknown>("/api/actions/run", {
        action,
        confirmation: action,
      });
      setActionResult(value);
      oauthResult.refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Schwab 重授权启动失败");
      oauthResult.refresh();
    } finally {
      setBusy(null);
    }
  }

  const oauthHealthLabel = oauth?.state === "EXPIRING"
    ? "建议提前重授权"
    : oauth?.action_required
      ? "需要操作"
      : "无需操作";
  const oauthFlowMessage = oauthFlowState === "ACTIVE"
    ? "新标签页已打开，正在等待 Schwab 回调。请勿重复点击。"
    : oauthFlowState === "SUCCEEDED"
      ? "重授权已成功，新的 Token 已由项目安全保存。"
      : oauthFlowState === "FAILED" || oauthFlowState === "INTERRUPTED"
        ? "上次授权未完成。关闭旧标签页后才能创建新流程。"
        : "手动启动时只会创建一个授权流程。";

  return (
    <ConsoleShell active="operations" eyebrow="No-LLM action center" title="操作中心">
      <DataBoundary loading={result.loading} error={result.error}>
        <div className="toolbar"><p>这些按钮直接调用确定性的本地服务，不需要 Codex/LLM。外部同步、通知和删除操作均需明确点击确认。</p><RefreshButton onClick={result.refresh} loading={result.loading} /></div>
        <Card className="action-console" kicker="OPERATIONS" title="常用操作">
          <div className="action-grid">
            <div><strong>监控与同步</strong><span>按调度规则运行，不强制制造重复 Run。</span><div><ActionButton onClick={() => runAction("monitor_run_due")} busy={busy === "monitor_run_due"}>运行到期 Monitor</ActionButton><ActionButton onClick={() => runAction("post_market_sync_due", "将连接已配置的美股账户与自选来源。确认运行到期收盘同步？")} busy={busy === "post_market_sync_due"}>运行收盘同步</ActionButton><ActionButton onClick={() => runAction("post_market_sync_catch_up", "将补跑最近一个缺少成功回执的美股收盘会话。继续？")} busy={busy === "post_market_sync_catch_up"}>补跑最近会话</ActionButton></div></div>
            <div><strong>通知</strong><span>测试会真实发送一条 Telegram；flush 只处理到期 Outbox。</span><div><ActionButton onClick={() => runAction("notification_test", "确认向已配置的 Telegram 发送测试消息？")} busy={busy === "notification_test"}>发送测试消息</ActionButton><ActionButton onClick={() => runAction("notification_flush")} busy={busy === "notification_flush"}>发送待处理通知</ActionButton></div></div>
            <div><strong>数据保全</strong><span>备份为 owner-only；缓存执行清理前可先预览。</span><div><ActionButton onClick={() => runAction("database_backup")} busy={busy === "database_backup"}>创建数据库备份</ActionButton><ActionButton onClick={() => runAction("cache_prune_preview")} busy={busy === "cache_prune_preview"}>预览 30 天缓存清理</ActionButton><ActionButton tone="warning" onClick={() => runAction("cache_prune_apply", "这会删除超过 30 天保留期的已过期 Provider/Reddit 缓存，不影响研究、Monitor 或账户历史。确认执行？")} busy={busy === "cache_prune_apply"}>执行缓存清理</ActionButton></div></div>
          </div>
          {actionError && <div className="inline-error">{actionError}</div>}
          {actionResult !== null && <div className="action-result"><div className="result-head"><span>最近操作回执</span><button type="button" onClick={() => setActionResult(null)}>清除</button></div><pre>{displayJson(actionResult)}</pre></div>}
        </Card>
        <div className="dashboard-grid">
          <Card className="span-6" kicker="POST-MARKET SYNC" title="收盘后同步">
            <div className="operation-hero"><Badge value={String(sync?.health ?? "—")} /><strong>{String(sync?.run_status ?? "无回执")}</strong><span>{String(sync?.receipt_session_date ?? "—")}</span></div>
            <dl className="detail-list"><div><dt>账户</dt><dd>{String(sync?.portfolio_status ?? "—")}</dd></div><div><dt>自选</dt><dd>{String(sync?.watchlist_status ?? "—")}</dd></div><div><dt>尝试次数</dt><dd>{String(sync?.attempt_count ?? 0)}</dd></div><div><dt>计划时间</dt><dd>{formatDate(sync?.expected_scheduled_for)}</dd></div></dl>
          </Card>
          <Card className="span-3" kicker="SCHWAB OAUTH" title="授权健康">
            <div className="operation-hero compact"><Badge value={String(oauth?.state ?? "—")} /><strong>{oauthHealthLabel}</strong><span>重授权截止 {formatDate(oauth?.reauthorization_due_at)}</span></div>
            <div className="oauth-control">
              <div><span>授权流程</span><Badge value={oauthConfigured ? oauthFlowState : "NOT CONFIGURED"} /></div>
              <p>{oauthFlowMessage}</p>
              {oauthRetryRequiresConfirmation ? (
                <ActionButton
                  tone="warning"
                  onClick={() => startSchwabReauthorization(true)}
                  busy={busy === "schwab_oauth_renew_confirmed"}
                >
                  关闭旧标签后重新授权
                </ActionButton>
              ) : (
                <ActionButton
                  onClick={() => startSchwabReauthorization(false)}
                  busy={busy === "schwab_oauth_renew" || oauthFlowState === "ACTIVE"}
                  busyLabel={oauthFlowState === "ACTIVE" ? "等待授权回调…" : "正在打开授权页…"}
                >
                  手动重新授权
                </ActionButton>
              )}
            </div>
          </Card>
          <Card className="span-3" kicker="OUTBOX" title="Telegram 通知">
            <div className="operation-hero compact"><Badge value={notification?.configured ? "HEALTHY" : "NOT CONFIGURED"} /><strong>{String(notification?.pending ?? 0)} 待发送</strong><span>{String(notification?.delivered ?? 0)} 已送达 · {String(notification?.dead_letter ?? 0)} dead</span></div>
          </Card>
          <Card className="span-4" kicker="DATABASE" title="本地数据">
            <div className="big-number">{formatBytes(maintenance?.database_bytes)}</div><p className="mono muted">{String(maintenance?.database_filename ?? "—")}</p>
            <dl className="detail-list"><div><dt>数据表</dt><dd>{tableCounts.length}</dd></div><div><dt>Provider cache</dt><dd>{String(maintenance?.provider_cache_total ?? 0)}</dd></div><div><dt>已过期</dt><dd>{String(maintenance?.provider_cache_expired ?? 0)}</dd></div></dl>
          </Card>
          <Card className="span-4" kicker="BACKUPS & ARTIFACTS" title="数据保全">
            <div className="metric-pairs single"><div><span>SQLite 备份</span><strong>{String(maintenance?.backup_files ?? 0)}</strong><small>最近 {formatDate(maintenance?.latest_backup_at)}</small></div><div><span>验证产物</span><strong>{String(maintenance?.validation_artifact_files ?? 0)}</strong><small>{formatBytes(maintenance?.validation_artifact_bytes)}</small></div></div>
            <code className="command-block">uv run trading-partner-maintenance backup</code>
          </Card>
          <Card className="span-4" kicker="RETENTION" title="保留策略">
            <div className="retention-list">{retention.map((rule) => <div key={String(rule.area)}><span>{String(rule.area)}</span><strong>{String(rule.policy).replaceAll("_", " ")}</strong><small>{rule.days ? `${String(rule.days)} days` : "不自动删除"}</small></div>)}</div>
          </Card>
          <Card className="span-12" kicker="TABLE INVENTORY" title="持久化数据量">
            <div className="table-inventory">{tableCounts.map((item) => <div key={String(item.table)}><span className="mono">{String(item.table)}</span><strong>{String(item.rows)}</strong></div>)}</div>
          </Card>
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
