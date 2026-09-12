type CycleCounts = {
  add_count?: unknown;
  reduce_count?: unknown;
  warning_codes?: unknown;
};

export const CYCLE_ACTIVITY_COUNTS_HELP =
  "Counts trade records: the initial buy is excluded; the final sale is included.";

function count(value: unknown): string {
  const parsed = typeof value === "number"
    ? value
    : typeof value === "string" && /^\d+$/.test(value.trim())
      ? Number(value.trim())
      : NaN;
  return Number.isSafeInteger(parsed) && parsed >= 0 ? String(parsed) : "—";
}

export function cycleActivityCounts(cycle: CycleCounts): string {
  if (Array.isArray(cycle.warning_codes)
    && cycle.warning_codes.includes("MANUAL_RECOMPUTE_REQUIRED")) return "— / —";
  return `${count(cycle.add_count)} / ${count(cycle.reduce_count)}`;
}
