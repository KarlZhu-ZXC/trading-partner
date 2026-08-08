type Dict = Record<string, unknown>;

export type MonitorRunTarget = {
  instrumentId: string | null;
  monitorId: string;
  monitorName: string | null;
  version: number | null;
};

export type MonitorRunPresentation = {
  nameLabel: string;
  symbolLabel: string;
  targets: MonitorRunTarget[];
};

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberValue(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function instrumentSymbol(instrumentId: string | null): string | null {
  if (!instrumentId) return null;
  const parts = instrumentId.split(":");
  return parts.at(-1) ?? instrumentId;
}

function shortMonitorId(monitorId: string): string {
  return monitorId.length > 16 ? `${monitorId.slice(0, 12)}…` : monitorId;
}

export function monitorRunTargets(run: Dict, dashboardItems: Dict[]): MonitorRunTarget[] {
  const currentById = new Map<string, Dict>();
  for (const item of dashboardItems) {
    const monitor = (item.monitor ?? {}) as Dict;
    const monitorId = stringValue(monitor.monitor_id);
    if (monitorId) currentById.set(monitorId, monitor);
  }

  const targetsById = new Map<string, Omit<MonitorRunTarget, "monitorName">>();
  const observations = Array.isArray(run.observations) ? run.observations : [];
  for (const value of observations) {
    if (!value || typeof value !== "object") continue;
    const observation = value as Dict;
    const monitorId = stringValue(observation.monitor_id);
    if (!monitorId) continue;
    const existing = targetsById.get(monitorId);
    targetsById.set(monitorId, {
      monitorId,
      instrumentId: existing?.instrumentId ?? stringValue(observation.instrument_id),
      version: existing?.version ?? numberValue(observation.monitor_version),
    });
  }

  const selectedMonitorIds = [
    ...stringList(run.selected_monitor_ids),
    ...stringList(run.requested_monitor_ids),
  ];
  for (const monitorId of selectedMonitorIds) {
    if (targetsById.has(monitorId)) continue;
    const current = currentById.get(monitorId);
    targetsById.set(monitorId, {
      monitorId,
      instrumentId: stringValue(current?.primary_instrument_id),
      version: numberValue(current?.version),
    });
  }

  return [...targetsById.values()].map((target) => {
    const current = currentById.get(target.monitorId);
    return {
      ...target,
      instrumentId: target.instrumentId ?? stringValue(current?.primary_instrument_id),
      // Historical runs may target an older immutable version, but the current
      // definition still supplies a much more useful identity than an opaque ID.
      monitorName: stringValue(current?.name),
    };
  });
}

export function monitorRunPresentation(run: Dict, dashboardItems: Dict[]): MonitorRunPresentation {
  const targets = monitorRunTargets(run, dashboardItems);
  const symbols = targets
    .map((target) => instrumentSymbol(target.instrumentId))
    .filter((symbol): symbol is string => Boolean(symbol));
  const visibleSymbols = symbols.slice(0, 3);
  const symbolLabel = visibleSymbols.length
    ? `${visibleSymbols.join(" / ")}${symbols.length > visibleSymbols.length ? ` +${symbols.length - visibleSymbols.length}` : ""}`
    : "Target not recorded";

  let nameLabel = "Unresolved Monitor";
  if (targets.length === 1) {
    const target = targets[0];
    nameLabel = target.monitorName
      ?? `Monitor ${shortMonitorId(target.monitorId)}${target.version === null ? "" : ` · v${target.version}`}`;
  } else if (targets.length > 1) {
    nameLabel = `${targets.length} Monitors`;
  }

  return { nameLabel, symbolLabel, targets };
}
