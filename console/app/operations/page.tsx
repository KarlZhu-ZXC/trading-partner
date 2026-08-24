"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ConsoleShell } from "../components/console-shell";
import { ConfirmationDialog, ErrorNote, ActionButton, Badge, Card, DataBoundary, MetricTile, PageActionMenu, displayJson, formatBytes, formatDate } from "../components/ui";
import { envelopeData, listOf, postApi, useApi } from "../lib/api";

type Dict = Record<string, unknown>;
type ConfirmationState = { title: string; description: string; confirmLabel?: string; tone?: "default" | "warning"; onConfirm: () => void };

export default function OperationsPage() {
  const result = useApi<Dict>("/api/operations");
  const oauthResult = useApi<Dict>("/api/schwab/oauth");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<unknown>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmationState | null>(null);
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
  const health = envelopeData<Dict>(result.data?.health);
  const healthComponents = Object.entries((health?.components as Dict | undefined) ?? {});
  const quality = health?.data_quality as Dict | undefined;
  const providerRoutes = listOf<Dict>(quality, "provider_routes");
  const monitorDashboard = envelopeData<Dict>(result.data?.monitor_dashboard);
  const monitorSchedules = listOf<Dict>(monitorDashboard, "items")
    .map((item) => ({ monitor: (item.monitor ?? {}) as Dict, item }))
    .sort((left, right) => String(left.item.next_due_at ?? "").localeCompare(String(right.item.next_due_at ?? "")));
  const syncReceipts = listOf<Dict>(result.data, "sync_receipts");
  const outboxEntries = listOf<Dict>(result.data, "outbox_entries");

  useEffect(() => {
    if (oauthFlowState !== "ACTIVE") return;
    const timer = window.setInterval(oauthResult.refresh, 1000);
    return () => window.clearInterval(timer);
  }, [oauthFlowState, oauthResult.refresh]);

  async function executeAction(action: string) {
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
      setActionError(error instanceof Error ? error.message : "Operation failed");
    } finally {
      setBusy(null);
    }
  }

  function runAction(action: string, warning?: string) {
    if (warning) {
      setConfirmation({
        title: "Confirm Operation",
        description: warning,
        confirmLabel: "Run Operation",
        tone: action.includes("prune_apply") ? "warning" : "default",
        onConfirm: () => { setConfirmation(null); void executeAction(action); },
      });
      return;
    }
    void executeAction(action);
  }

  async function executeSchwabReauthorization(confirmRetryAfterFailure = false) {
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
      setActionError(error instanceof Error ? error.message : "Unable to start Schwab reauthorization");
      oauthResult.refresh();
    } finally {
      setBusy(null);
    }
  }

  function startSchwabReauthorization(confirmRetryAfterFailure = false) {
    const warning = confirmRetryAfterFailure
      ? "Confirm that the previous Schwab authorization tab is closed. A new OAuth state and browser tab will be created. Continue?"
      : "A new Schwab authorization tab will open and the project will wait up to five minutes for the local callback. Use only the newly opened tab. Continue?";
    setConfirmation({
      title: "Confirm Schwab Reauthorization",
      description: warning,
      confirmLabel: "Open Authorization",
      onConfirm: () => { setConfirmation(null); void executeSchwabReauthorization(confirmRetryAfterFailure); },
    });
  }

  const oauthHealthLabel = oauth?.state === "EXPIRING"
    ? "Reauthorize soon"
    : oauth?.action_required
      ? "Action required"
      : "No action required";
  const oauthFlowMessage = oauthFlowState === "ACTIVE"
    ? "A new tab is open and waiting for the Schwab callback. Do not start another flow."
    : oauthFlowState === "SUCCEEDED"
      ? "Reauthorization succeeded and the new token was stored securely."
      : oauthFlowState === "FAILED" || oauthFlowState === "INTERRUPTED"
        ? "The previous authorization did not complete. Close the old tab before starting a new flow."
        : "A manual start creates exactly one authorization flow.";

  return (
    <ConsoleShell active="operations" pageActions={<PageActionMenu ariaLabel="Operations Page Actions" items={[
      { id: "refresh", label: result.loading ? "Refreshing…" : "Refresh", description: "Reload local operational state", icon: <RefreshCw aria-hidden="true" className={result.loading ? "spin" : undefined} />, disabled: result.loading, onSelect: result.refresh },
    ]} />}>
      <DataBoundary loading={result.loading} error={result.error}>
        <Card className="action-console" kicker="OPERATIONS" title="Common Actions">
          <div className="action-grid">
            <div><strong>Monitoring & Sync</strong><span>Run only what is due without forcing duplicate Runs.</span><div><ActionButton onClick={() => runAction("monitor_run_due")} busy={busy === "monitor_run_due"}>Run Due Monitors</ActionButton><ActionButton onClick={() => runAction("post_market_sync_due", "This connects to configured US account and Watchlist sources. Run the due post-market sync?")} busy={busy === "post_market_sync_due"}>Run Post-Market Sync</ActionButton><ActionButton onClick={() => runAction("post_market_sync_catch_up", "This reruns the latest US market session without a successful receipt. Continue?")} busy={busy === "post_market_sync_catch_up"}>Catch Up Latest Session</ActionButton></div></div>
            <div><strong>Notifications</strong><span>The test sends a real Telegram message; flush processes only due Outbox entries.</span><div><ActionButton onClick={() => runAction("notification_test", "Send a test message to the configured Telegram destination?")} busy={busy === "notification_test"}>Send Test Message</ActionButton><ActionButton onClick={() => runAction("notification_flush")} busy={busy === "notification_flush"}>Send Pending Notifications</ActionButton></div></div>
            <div><strong>Data Protection</strong><span>Backups are owner-only; cache pruning can be previewed before applying.</span><div><ActionButton onClick={() => runAction("database_backup")} busy={busy === "database_backup"}>Create Database Backup</ActionButton><ActionButton onClick={() => runAction("cache_prune_preview")} busy={busy === "cache_prune_preview"}>Preview 30-Day Cache Prune</ActionButton><ActionButton tone="warning" onClick={() => runAction("cache_prune_apply", "This deletes expired Provider/Reddit cache older than 30 days. Research, Monitor, and account history are unaffected. Continue?")} busy={busy === "cache_prune_apply"}>Apply Cache Prune</ActionButton></div></div>
          </div>
          <ErrorNote>{actionError}</ErrorNote>
          {actionResult !== null && <div className="action-result"><div className="result-head"><span>Latest Operation Receipt</span><button type="button" onClick={() => setActionResult(null)}>Clear</button></div><pre>{displayJson(actionResult)}</pre></div>}
        </Card>
        <div className="dashboard-grid">
          <Card className="span-6" kicker="POST-MARKET SYNC" title="Post-Market Sync">
            <div className="operation-hero"><Badge value={String(sync?.health ?? "—")} /><strong>{String(sync?.run_status ?? "No receipt")}</strong><span>{String(sync?.receipt_session_date ?? "—")}</span></div>
            <dl className="detail-list"><div><dt>Accounts</dt><dd>{String(sync?.portfolio_status ?? "—")}</dd></div><div><dt>Watchlist</dt><dd>{String(sync?.watchlist_status ?? "—")}</dd></div><div><dt>Attempts</dt><dd>{String(sync?.attempt_count ?? 0)}</dd></div><div><dt>Scheduled For</dt><dd>{formatDate(sync?.expected_scheduled_for)}</dd></div></dl>
          </Card>
          <Card className="span-3" kicker="SCHWAB OAUTH" title="Authorization Health">
            <div className="operation-hero compact"><Badge value={String(oauth?.state ?? "—")} /><strong>{oauthHealthLabel}</strong><span>Reauthorize By {formatDate(oauth?.reauthorization_due_at)}</span></div>
            <div className="oauth-control">
              <div><span>Authorization Flow</span><Badge value={oauthConfigured ? oauthFlowState : "NOT CONFIGURED"} /></div>
              <p>{oauthFlowMessage}</p>
              {oauthRetryRequiresConfirmation ? (
                <ActionButton
                  tone="warning"
                  onClick={() => startSchwabReauthorization(true)}
                  busy={busy === "schwab_oauth_renew_confirmed"}
                >
                  Reauthorize After Closing Old Tab
                </ActionButton>
              ) : (
                <ActionButton
                  onClick={() => startSchwabReauthorization(false)}
                  busy={busy === "schwab_oauth_renew" || oauthFlowState === "ACTIVE"}
                  busyLabel={oauthFlowState === "ACTIVE" ? "Waiting for callback…" : "Opening authorization page…"}
                >
                  Reauthorize Manually
                </ActionButton>
              )}
            </div>
          </Card>
          <Card className="span-3" kicker="OUTBOX" title="Telegram Notifications">
            <div className="operation-hero compact"><Badge value={notification?.configured ? "HEALTHY" : "NOT CONFIGURED"} /><strong>{String(notification?.pending ?? 0)} pending</strong><span>{String(notification?.delivered ?? 0)} delivered · {String(notification?.dead_letter ?? 0)} dead</span></div>
          </Card>
          <Card className="span-4" kicker="DATABASE" title="Local Data">
            <div className="big-number">{formatBytes(maintenance?.database_bytes)}</div><p className="mono muted">{String(maintenance?.database_filename ?? "—")}</p>
            <dl className="detail-list"><div><dt>Tables</dt><dd>{tableCounts.length}</dd></div><div><dt>Provider Cache</dt><dd>{String(maintenance?.provider_cache_total ?? 0)}</dd></div><div><dt>Expired</dt><dd>{String(maintenance?.provider_cache_expired ?? 0)}</dd></div></dl>
          </Card>
          <Card className="span-4" kicker="BACKUPS & ARTIFACTS" title="Data Protection">
            <div className="metric-pairs single"><MetricTile label="SQLite Backups" value={String(maintenance?.backup_files ?? 0)} detail={<>Latest {formatDate(maintenance?.latest_backup_at)}</>} /><MetricTile label="Validation Artifacts" value={String(maintenance?.validation_artifact_files ?? 0)} detail={formatBytes(maintenance?.validation_artifact_bytes)} /></div>
            <code className="command-block">uv run trading-partner-maintenance backup</code>
          </Card>
          <Card className="span-4" kicker="RETENTION" title="Retention Policy">
            <div className="retention-list">{retention.map((rule) => <div key={String(rule.area)}><span>{String(rule.area)}</span><strong>{String(rule.policy).replaceAll("_", " ")}</strong><small>{rule.days ? `${String(rule.days)} days` : "Never deleted automatically"}</small></div>)}</div>
          </Card>
          <Card className="span-12" kicker="TABLE INVENTORY" title="Durable Data Volume">
            <div className="table-inventory">{tableCounts.map((item) => <div key={String(item.table)}><span className="mono">{String(item.table)}</span><strong>{String(item.rows)}</strong></div>)}</div>
          </Card>
          <Card className="span-12" kicker="SYNC RECEIPTS" title="Post-Market Sync History">
            <div className="table-wrap"><table><thead><tr><th>Session</th><th>Completed</th><th>Accounts</th><th>Watchlist</th><th>Snapshots</th><th>Attempts</th><th>Errors</th><th>Status</th></tr></thead><tbody>{syncReceipts.map((receipt) => <tr key={String(receipt.run_id)}><td><strong>{String(receipt.market_session_date)}</strong><small className="mono">{String(receipt.run_id)}</small></td><td>{formatDate(receipt.completed_at)}</td><td>{String(receipt.portfolio_status)}</td><td>{String(receipt.watchlist_status)}</td><td>{String(receipt.account_snapshot_count ?? 0)}</td><td>{String(receipt.attempt_count ?? 0)}</td><td className="mono">{listOf<string>(receipt, "error_codes").join(" · ") || "—"}</td><td><Badge value={String(receipt.status)} /></td></tr>)}</tbody></table></div>
          </Card>
          <Card className="span-12" kicker="OUTBOX DELIVERY" title="Notification Delivery & Dead Letter">
            <p className="card-note">Only title, source, and delivery metadata are shown to avoid exposing message bodies or authorization notes.</p>
            <div className="table-wrap"><table><thead><tr><th>Notification</th><th>Source</th><th>Created</th><th>Last Attempt / Delivered</th><th>Attempts</th><th>Error</th><th>Status</th></tr></thead><tbody>{outboxEntries.map((entry) => <tr key={String(entry.notification_id)}><td><strong>{String(entry.title)}</strong><small className="mono">{String(entry.notification_id)}</small></td><td><strong>{String(entry.source_type)}</strong><small className="mono">{String(entry.source_id)}</small></td><td>{formatDate(entry.created_at)}</td><td>{formatDate(entry.delivered_at ?? entry.last_attempt_at)}</td><td>{String(entry.attempt_count ?? 0)}</td><td className="mono">{String(entry.last_error_code ?? "—")}</td><td><Badge value={String(entry.status)} /></td></tr>)}</tbody></table></div>
          </Card>
          <Card className="span-6" kicker="SCHEDULER" title="Monitor Schedule & Next Due" action={<Badge value={monitorSchedules.some(({ item }) => item.schedule_health !== "OK") ? "ATTENTION" : "READY"} />}>
            <p className="card-note">This shows definition-level schedule health and next due time. launchd installation remains an explicit local command and page loads never change system configuration.</p>
            <dl className="detail-list"><div><dt>LaunchAgent Plist</dt><dd><Badge value={maintenance?.monitor_scheduler_plist_present ? "INSTALLED" : "MISSING"} /></dd></div><div><dt>launchd Loaded</dt><dd><Badge value={maintenance?.monitor_scheduler_loaded === true ? "LOADED" : maintenance?.monitor_scheduler_loaded === false ? "NOT LOADED" : "UNKNOWN"} /></dd></div><div><dt>Last Exit</dt><dd>{String(maintenance?.monitor_scheduler_last_exit_code ?? "—")}</dd></div></dl>
            <div className="operations-detail-list">{monitorSchedules.length === 0 ? <span className="muted">No active Monitors.</span> : monitorSchedules.map(({ monitor, item }) => <div key={String(monitor.monitor_id)}><div><strong>{String(monitor.name ?? "Untitled Monitor")}</strong><small className="mono">{String(monitor.primary_instrument_id ?? "portfolio")}</small></div><div><Badge value={String(item.schedule_health ?? "UNKNOWN")} /><small>Next {formatDate(item.next_due_at)}</small></div></div>)}</div>
            <code className="command-block">uv run trading-partner-monitor-scheduler status</code>
            <code className="command-block">uv run trading-partner-monitor-scheduler install</code>
          </Card>
          <Card className="span-6" kicker="CONFIGURATION READINESS" title="Component Readiness Matrix" action={<Badge value={String(health?.status ?? "UNKNOWN")} />}>
            <p className="card-note">configuration means configured readiness only; only checks marked live_probe represent actual reachability.</p>
            <div className="configuration-matrix">{healthComponents.map(([name, raw]) => { const component = raw as Dict; return <div key={name}><strong>{name.replaceAll("_", " ")}</strong><Badge value={String(component.state ?? "UNKNOWN")} /><span>{String(component.check_kind ?? "configuration")}</span><small>{String(component.detail ?? component.message ?? "—")}</small></div>; })}</div>
          </Card>
          <Card className="span-12" kicker="PROVIDER ROUTES · LAST 24H" title="Routing & Admission Results">
            <div className="provider-route-table table-wrap"><table><thead><tr><th>Market / Category</th><th>Latest</th><th>Selected</th><th>Calls</th><th>Fallback</th><th>Failures</th><th>Latest Error</th></tr></thead><tbody>{providerRoutes.length === 0 ? <tr><td colSpan={7}>No durable Provider route receipts in the last 24 hours.</td></tr> : providerRoutes.map((route) => <tr key={`${String(route.market)}-${String(route.category)}`}><td><strong>{String(route.market)}</strong><small>{String(route.category)}</small></td><td>{formatDate(route.latest_at)}</td><td>{String(route.latest_selected_vendor ?? "—")}</td><td>{String(route.execution_count ?? 0)}</td><td className={Number(route.fallback_count ?? 0) > 0 ? "text-amber" : ""}>{String(route.fallback_count ?? 0)}</td><td className={Number(route.failure_count ?? 0) > 0 ? "text-red" : ""}>{String(route.failure_count ?? 0)}</td><td className="mono">{String(route.latest_error_code ?? "—")}</td></tr>)}</tbody></table></div>
            {quality?.provider_route_window_truncated === true && <p className="card-note text-amber">The durable read limit was reached; this view is not the complete history.</p>}
          </Card>
        </div>
        <ConfirmationDialog
          open={confirmation !== null}
          title={confirmation?.title ?? "Confirm Operation"}
          description={confirmation?.description}
          confirmLabel={confirmation?.confirmLabel}
          tone={confirmation?.tone}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => confirmation?.onConfirm()}
        />
      </DataBoundary>
    </ConsoleShell>
  );
}
