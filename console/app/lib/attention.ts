import { monitorAnchorId } from "../components/ui";
import { monitorRunTargets } from "./monitor-runs";

type Dict = Record<string, unknown>;

export type ConsoleNotice = {
  detail: string;
  href: string;
  key: string;
  severity: string;
  title: string;
};

export type ConsoleNoticeGroups = {
  actionItems: ConsoleNotice[];
  automaticItems: ConsoleNotice[];
  qualityItems: ConsoleNotice[];
};

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

function formatRunTime(value: unknown): string {
  if (!value || typeof value !== "string") return "时间未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function instrumentSymbol(instrumentId: unknown): string {
  if (typeof instrumentId !== "string" || !instrumentId) return "未知标的";
  return instrumentId.split(":").at(-1) ?? instrumentId;
}

function monitorInfo(item: Dict): { id: string; name: string; status: string; symbol: string } | null {
  const monitor = (item.monitor ?? {}) as Dict;
  const id = stringValue(monitor.monitor_id);
  if (!id) return null;
  return {
    id,
    name: stringValue(monitor.name) ?? "未命名 Monitor",
    status: String(monitor.status ?? "UNKNOWN").toUpperCase(),
    symbol: instrumentSymbol(monitor.primary_instrument_id),
  };
}

function runReason(run: Dict): string {
  const codes = [...stringList(run.error_codes), ...stringList(run.warning_codes)];
  if (codes.includes("OIL_WEEKEND_REFERENCE_UNAVAILABLE")) {
    return "周末备用参考源在该次运行不可用；下一次计划运行会重新评估，不代表当前仍无数据";
  }
  if (codes.includes("MARKET_CLOSED")) return "市场休市，调度器会在下一观察窗口自动恢复";
  if (codes.includes("NO_ACTIVE_MONITORS")) return "目标 Monitor 已停止或归档";
  if (codes.includes("PROVIDER_ADMISSION_TIMEOUT")) return "Provider 请求排队超时，下一次调度会自动重试";
  if (codes.includes("PROVIDER_RATE_LIMIT_ERROR")) return "上游 Provider 限流，下一次调度会自动重试";
  if (codes.includes("DATA_CONTRACT_ERROR")) return "上游数据格式与预期不符，需要检查 Provider 路由";
  if (codes.length > 0) return `原因代码：${codes.slice(0, 2).join(" / ")}`;
  return "运行未完成，详情中没有记录明确原因";
}

function isAutomaticRun(run: Dict): boolean {
  const codes = new Set([...stringList(run.error_codes), ...stringList(run.warning_codes)]);
  return [
    "OIL_WEEKEND_REFERENCE_UNAVAILABLE",
    "MARKET_CLOSED",
    "PROVIDER_ADMISSION_TIMEOUT",
    "PROVIDER_RATE_LIMIT_ERROR",
  ].some((code) => codes.has(code));
}

function accountProviderLabel(value: unknown): string {
  const provider = String(value ?? "账户").toLowerCase();
  if (provider === "moomoo") return "Moomoo";
  if (provider === "schwab") return "Schwab";
  return provider === "账户" ? provider : provider.toUpperCase();
}

function qualityIssueHref(issue: Dict): string {
  const scope = String(issue.scope ?? "");
  const subject = String(issue.subject_ref ?? "");
  if (scope === "monitor" && subject) return `/monitors#${monitorAnchorId(subject)}`;
  if (scope === "research_state" && subject) return `/research#subject-${subject}`;
  if (scope === "account_snapshot") return "/portfolio#holdings";
  if (scope === "account_activity") return "/portfolio#activity";
  if (scope === "provider_route" || scope === "persistence") return "/operations";
  return "/capabilities";
}

function qualityNotice(
  issue: Dict,
  monitorItems: Dict[],
  accountProviderByRef: Map<string, string>,
  activityByRef: Map<string, Dict>,
  routeByRef: Map<string, Dict>,
): ConsoleNotice {
  const code = String(issue.code ?? "DATA_QUALITY_ISSUE");
  const subject = String(issue.subject_ref ?? "");
  const monitorItem = monitorItems.find((item) => monitorInfo(item)?.id === subject);
  const monitor = monitorItem ? monitorInfo(monitorItem) : null;
  const provider = accountProviderLabel(accountProviderByRef.get(subject));
  const observedAt = formatRunTime(issue.observed_at);
  const href = qualityIssueHref(issue);

  if (code === "MONITOR_LATEST_NOT_EVALUATED" && monitorItem && monitor) {
    const quality = (monitorItem.latest_run ?? {}) as Dict;
    const states = Array.isArray(monitorItem.rule_states) ? monitorItem.rule_states : [];
    const count = states.filter(
      (value) => value && typeof value === "object" && String((value as Dict).state) === "NOT_EVALUATED",
    ).length;
    const reason = stringList(quality.warning_codes).includes("OIL_WEEKEND_REFERENCE_UNAVAILABLE")
      ? "周末备用参考源仅在该次运行不可用；下一次计划运行会重新评估"
      : "最近一次运行存在无法评估的规则，请查看事实新鲜度与 Provider 回执";
    return {
      key: `quality-${code}-${subject}`,
      severity: "WAITING",
      title: `${monitor.symbol} · ${monitor.name}`,
      detail: `${observedAt} 运行 · ${count || "部分"} 条规则未评估 · ${reason}`,
      href,
    };
  }

  if (code === "MONITOR_CURRENT_VERSION_NEVER_EVALUATED" && monitor) {
    return {
      key: `quality-${code}-${subject}`,
      severity: "ATTENTION",
      title: `${monitor.symbol} · ${monitor.name}`,
      detail: `当前 v${String(((monitorItem?.monitor ?? {}) as Dict).version ?? "—")} 尚未运行，请执行一次 Monitor Run`,
      href,
    };
  }

  if (code === "ACCOUNT_SNAPSHOT_DEGRADED") {
    return {
      key: `quality-${code}-${subject}`,
      severity: "LIMITATION",
      title: `${provider} 账户快照带数据限制`,
      detail: `${observedAt} 快照 · 估值可用；具体限制见账户数据质量说明`,
      href,
    };
  }

  if (code === "ACCOUNT_PRICE_TIME_INCOMPLETE") {
    return {
      key: `quality-${code}-${subject}`,
      severity: "LIMITATION",
      title: `${provider} 持仓价格缺少券商原始时间`,
      detail: `${observedAt} 快照 · 这是接口/接入覆盖说明，不需要用户操作`,
      href,
    };
  }

  if (code === "ACCOUNT_ACTIVITY_COVERAGE_INCOMPLETE") {
    const receipt = activityByRef.get(subject) ?? {};
    const gaps = stringList(receipt.gap_codes);
    const gapText = gaps.includes("ACCOUNT_SNAPSHOTS_UNAVAILABLE")
      ? "所选交易窗口内没有可用于对账的账户快照"
      : "部分活动类型或费用尚未完整接入";
    return {
      key: `quality-${code}-${subject}`,
      severity: "LIMITATION",
      title: `${provider} 账户活动覆盖不完整`,
      detail: `${formatRunTime(receipt.fetched_at ?? issue.observed_at)} 回执 · ${gapText}；不要求用户补数据`,
      href,
    };
  }

  if (code === "PROVIDER_ROUTE_FAILURES_RECENT") {
    const route = routeByRef.get(subject) ?? {};
    return {
      key: `quality-${code}-${subject}`,
      severity: "OBSERVE",
      title: `${subject || "Provider"} 最近 24h 有失败回执`,
      detail: `${String(route.failure_count ?? "部分")} / ${String(route.execution_count ?? "—")} 次失败 · 最近 ${formatRunTime(route.latest_at ?? issue.observed_at)} · ${String(route.latest_error_code ?? "原因未记录")}`,
      href,
    };
  }

  return {
    key: `quality-${code}-${subject}`,
    severity: String(issue.severity ?? "ATTENTION").toUpperCase(),
    title: code.replaceAll("_", " "),
    detail: `${observedAt} · ${String(issue.detail ?? "查看详情")}`,
    href,
  };
}

function dedupeQuality(items: ConsoleNotice[]): ConsoleNotice[] {
  const grouped = new Map<string, ConsoleNotice & { count: number }>();
  for (const item of items) {
    const key = `${item.severity}:${item.title}`;
    const current = grouped.get(key);
    if (current) {
      current.count += 1;
      continue;
    }
    grouped.set(key, { ...item, count: 1 });
  }
  return [...grouped.values()].map(({ count, ...item }) => ({
    ...item,
    title: count > 1 ? `${item.title} · ${count} 个账户` : item.title,
  }));
}

export function buildConsoleNotices({
  monitorItems,
  runs,
  researchAttention,
  notifications,
  qualityIssues,
  qualityAccounts,
  qualityActivity,
  qualityRoutes,
}: {
  monitorItems: Dict[];
  notifications?: Dict;
  qualityAccounts: Dict[];
  qualityActivity: Dict[];
  qualityIssues: Dict[];
  qualityRoutes: Dict[];
  researchAttention: Dict[];
  runs: Dict[];
}): ConsoleNoticeGroups {
  const actionItems: ConsoleNotice[] = researchAttention.map((item) => ({
    key: `research-${String(item.subject_id)}`,
    severity: "ATTENTION",
    title: `${String(item.title ?? "Research Subject")} · ${String(item.pending_count)} 个候选待确认`,
    detail: "需要你确认、拒绝或撤回候选变更",
    href: `/research#subject-${String(item.subject_id)}`,
  }));
  const automaticItems: ConsoleNotice[] = [];

  const activeLatestRunByMonitor = new Map<string, string>();
  for (const item of monitorItems) {
    const monitor = monitorInfo(item);
    if (!monitor || monitor.status !== "ACTIVE") continue;
    const runId = stringValue(((item.latest_run ?? {}) as Dict).run_id);
    if (runId) activeLatestRunByMonitor.set(monitor.id, runId);
  }

  for (const run of runs) {
    const status = String(run.status ?? "UNKNOWN").toUpperCase();
    if (["SUCCEEDED", "SKIPPED"].includes(status)) continue;
    const runId = stringValue(run.run_id);
    if (!runId) continue;
    const targets = monitorRunTargets(run, monitorItems).filter(
      (target) => activeLatestRunByMonitor.get(target.monitorId) === runId,
    );
    for (const target of targets) {
      const item = monitorItems.find((candidate) => monitorInfo(candidate)?.id === target.monitorId);
      const info = item ? monitorInfo(item) : null;
      if (!info) continue;
      const notice: ConsoleNotice = {
        key: `run-${runId}-${target.monitorId}`,
        severity: isAutomaticRun(run) ? "WAITING" : status === "FAILED" ? "ERROR" : "ATTENTION",
        title: `${info.symbol} · ${info.name}`,
        detail: `${formatRunTime(run.completed_at ?? run.started_at)} 运行${status === "FAILED" ? "失败" : "未完整完成"} · ${runReason(run)}`,
        href: `/monitors#${monitorAnchorId(target.monitorId)}`,
      };
      (isAutomaticRun(run) ? automaticItems : actionItems).push(notice);
    }
  }

  if (Number(notifications?.dead_letter ?? 0) > 0) {
    actionItems.push({
      key: "outbox-dead",
      severity: "ERROR",
      title: `${String(notifications?.dead_letter)} 条通知投递失败`,
      detail: "需要检查通知配置与投递回执",
      href: "/operations",
    });
  }

  const accountProviderByRef = new Map(
    qualityAccounts.map((item) => [String(item.account_ref), String(item.provider)]),
  );
  const activityByRef = new Map(qualityActivity.map((item) => [String(item.account_ref), item]));
  const routeByRef = new Map(
    qualityRoutes.map((item) => [`${String(item.market)}:${String(item.category)}`, item]),
  );
  const seenMonitorRun = new Set([
    ...actionItems.filter((item) => item.href.startsWith("/monitors#")).map((item) => item.href),
    ...automaticItems.filter((item) => item.href.startsWith("/monitors#")).map((item) => item.href),
  ]);
  const qualityNotices = qualityIssues
    .map((issue) => qualityNotice(issue, monitorItems, accountProviderByRef, activityByRef, routeByRef))
    .filter((item) => !(item.href.startsWith("/monitors#") && seenMonitorRun.has(item.href)));

  for (const item of qualityNotices) {
    if (item.severity === "ATTENTION" || item.severity === "ERROR") actionItems.push(item);
    else if (item.severity === "WAITING") automaticItems.push(item);
  }

  return {
    actionItems,
    automaticItems,
    qualityItems: dedupeQuality(
      qualityNotices.filter((item) => !["ATTENTION", "ERROR", "WAITING"].includes(item.severity)),
    ),
  };
}
