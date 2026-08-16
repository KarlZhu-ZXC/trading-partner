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
  if (!value || typeof value !== "string") return "Time not recorded";
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
  if (typeof instrumentId !== "string" || !instrumentId) return "Unknown target";
  return instrumentId.split(":").at(-1) ?? instrumentId;
}

function monitorInfo(item: Dict): { id: string; name: string; status: string; symbol: string } | null {
  const monitor = (item.monitor ?? {}) as Dict;
  const id = stringValue(monitor.monitor_id);
  if (!id) return null;
  return {
    id,
    name: stringValue(monitor.name) ?? "Untitled Monitor",
    status: String(monitor.status ?? "UNKNOWN").toUpperCase(),
    symbol: instrumentSymbol(monitor.primary_instrument_id),
  };
}

function runReason(run: Dict): string {
  const codes = [...stringList(run.error_codes), ...stringList(run.warning_codes)];
  if (codes.includes("OIL_WEEKEND_REFERENCE_UNAVAILABLE")) {
    return "The weekend reference was unavailable for this run; the next scheduled run will evaluate again and this does not mean data is currently unavailable";
  }
  if (codes.includes("MARKET_CLOSED")) return "The market is closed; the scheduler will resume at the next observation window";
  if (codes.includes("NO_ACTIVE_MONITORS")) return "The target Monitor is paused or archived";
  if (codes.includes("PROVIDER_ADMISSION_TIMEOUT")) return "The Provider request timed out in the local queue and will retry on the next schedule";
  if (codes.includes("PROVIDER_RATE_LIMIT_ERROR")) return "The upstream Provider rate-limited the request and the next schedule will retry";
  if (codes.includes("DATA_CONTRACT_ERROR")) return "The upstream data format did not match the contract; inspect the Provider route";
  if (codes.length > 0) return `Reason codes: ${codes.slice(0, 2).join(" / ")}`;
  return "The run did not complete and no explicit reason was recorded";
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
  const provider = String(value ?? "account").toLowerCase();
  if (provider === "moomoo") return "Moomoo";
  if (provider === "schwab") return "Schwab";
  return provider === "account" ? provider : provider.toUpperCase();
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

function recommendedActionLabel(value: unknown): string | null {
  return ({
    EVALUATE_MONITOR: "run the current Monitor version",
    INSPECT_MONITOR_RUN: "inspect the latest Monitor observations and typed errors",
    SYNC_ACCOUNT_TRANSACTIONS: "explicitly sync account transactions",
    SYNC_ACCOUNTS: "explicitly refresh the durable account snapshot",
    INSPECT_ACCOUNT_LIMITATIONS: "review the broker coverage limitations",
    INSPECT_PROVIDER_ROUTE: "inspect the latest secret-safe Provider route receipt",
    REVIEW_RESEARCH_STATE: "review the Research Subject lifecycle",
    CHECK_LOCAL_STORAGE: "check local database and backup health",
  } as Record<string, string>)[String(value ?? "")] ?? null;
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
      ? "The weekend reference was unavailable only for this run; the next scheduled run will evaluate again"
      : "The latest run contains unevaluated rules; inspect fact freshness and Provider receipts";
    return {
      key: `quality-${code}-${subject}`,
      severity: "WAITING",
      title: `${monitor.symbol} · ${monitor.name}`,
      detail: `${observedAt} run · ${count || "some"} rules not evaluated · ${reason}`,
      href,
    };
  }

  if (code === "MONITOR_CURRENT_VERSION_NEVER_EVALUATED" && monitor) {
    return {
      key: `quality-${code}-${subject}`,
      severity: "ATTENTION",
      title: `${monitor.symbol} · ${monitor.name}`,
      detail: `Current v${String(((monitorItem?.monitor ?? {}) as Dict).version ?? "—")} has not run; execute a Monitor Run`,
      href,
    };
  }

  if (code === "ACCOUNT_SNAPSHOT_DEGRADED") {
    return {
      key: `quality-${code}-${subject}`,
      severity: "LIMITATION",
      title: `${provider} account snapshot has data limitations`,
      detail: `${observedAt} snapshot · valuation is available; see account data-quality details for limitations`,
      href,
    };
  }

  if (code === "ACCOUNT_PRICE_TIME_INCOMPLETE") {
    return {
      key: `quality-${code}-${subject}`,
      severity: "LIMITATION",
      title: `${provider} position prices lack broker-native timestamps`,
      detail: `${observedAt} snapshot · this is an API coverage disclosure and requires no user action`,
      href,
    };
  }

  if (code === "ACCOUNT_ACTIVITY_COVERAGE_INCOMPLETE") {
    const receipt = activityByRef.get(subject) ?? {};
    const gaps = stringList(receipt.gap_codes);
    const gapText = gaps.includes("ACCOUNT_SNAPSHOTS_UNAVAILABLE")
      ? "No account snapshot is available for reconciliation in the selected window"
      : "Some activity categories or fees are not fully available";
    return {
      key: `quality-${code}-${subject}`,
      severity: "LIMITATION",
      title: `${provider} account activity coverage is incomplete`,
      detail: `${formatRunTime(receipt.fetched_at ?? issue.observed_at)} receipt · ${gapText}; no user-supplied data is required`,
      href,
    };
  }

  if (code === "PROVIDER_ROUTE_FAILURES_RECENT") {
    const route = routeByRef.get(subject) ?? {};
    return {
      key: `quality-${code}-${subject}`,
      severity: "OBSERVE",
      title: `${subject || "Provider"} has failed receipts in the last 24h`,
      detail: `${String(route.failure_count ?? "some")} / ${String(route.execution_count ?? "—")} failed · latest ${formatRunTime(route.latest_at ?? issue.observed_at)} · ${String(route.latest_error_code ?? "reason not recorded")}`,
      href,
    };
  }

  return {
    key: `quality-${code}-${subject}`,
    severity: String(issue.severity ?? "ATTENTION").toUpperCase(),
    title: code.replaceAll("_", " "),
    detail: `${observedAt} · ${String(issue.detail ?? "View details")}`,
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
    title: count > 1 ? `${item.title} · ${count} accounts` : item.title,
  }));
}

export function buildConsoleNotices({
  monitorItems,
  runs,
  researchAttention,
  workflowAttention = [],
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
  workflowAttention?: Dict[];
}): ConsoleNoticeGroups {
  const actionItems: ConsoleNotice[] = workflowAttention.map((item) => ({
    key: String(item.key ?? `workflow-${String(item.source_ref ?? "unknown")}`),
    severity: String(item.severity ?? "ATTENTION").toUpperCase(),
    title: String(item.title ?? "Workflow review required"),
    detail: String(item.detail ?? "Inspect the linked durable workflow state"),
    href: String(item.href ?? "/"),
  }));
  actionItems.push(...researchAttention.map((item) => ({
    key: `research-${String(item.subject_id)}`,
    severity: "ATTENTION",
    title: `${String(item.title ?? "Research Subject")} · ${String(item.pending_count)} candidates awaiting review`,
    detail: "Confirm, reject, or withdraw the proposed changes",
    href: `/research#subject-${String(item.subject_id)}`,
  })));
  const automaticItems: ConsoleNotice[] = [];

  const activeLatestRunByMonitor = new Map<string, string>();
  for (const item of monitorItems) {
    const monitor = monitorInfo(item);
    if (!monitor || monitor.status !== "ACTIVE") continue;
    const runId = stringValue(((item.latest_run ?? {}) as Dict).run_id);
    if (runId) activeLatestRunByMonitor.set(monitor.id, runId);
    const judgment = (item.latest_judgment ?? {}) as Dict;
    if (String(judgment.status ?? "").toUpperCase() === "FAILED") {
      const codes = stringList(judgment.error_codes);
      automaticItems.push({
        key: `judgment-${monitor.id}-${String(judgment.judgment_id ?? "latest")}`,
        severity: "WAITING",
        title: `${monitor.symbol} · composite judgment unavailable`,
        detail: `Deterministic rules remain valid; the next material evaluation will retry${codes.length ? ` · ${codes.slice(0, 2).join(" / ")}` : ""}`,
        href: `/monitors#${monitorAnchorId(monitor.id)}`,
      });
    }
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
        detail: `${formatRunTime(run.completed_at ?? run.started_at)} run ${status === "FAILED" ? "failed" : "completed partially"} · ${runReason(run)}`,
        href: `/monitors#${monitorAnchorId(target.monitorId)}`,
      };
      (isAutomaticRun(run) ? automaticItems : actionItems).push(notice);
    }
  }

  if (Number(notifications?.dead_letter ?? 0) > 0) {
    actionItems.push({
      key: "outbox-dead",
      severity: "ERROR",
      title: `${String(notifications?.dead_letter)} notification deliveries failed`,
      detail: "Inspect notification configuration and delivery receipts",
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
    .map((issue) => {
      const notice = qualityNotice(issue, monitorItems, accountProviderByRef, activityByRef, routeByRef);
      const next = recommendedActionLabel(issue.recommended_action_code);
      return next ? { ...notice, detail: `${notice.detail} · Next: ${next}` } : notice;
    })
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
