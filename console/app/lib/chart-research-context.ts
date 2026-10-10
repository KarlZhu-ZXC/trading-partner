import type { InteractiveTechnicalScene, TechnicalChartTimeframe } from "../components/interactive-technical-chart";
import { DATE_LABEL_TIME_LIMITATION, type ChartDisclosure } from "./technical-disclosures.ts";

export function hasDateLabelTimes(timeframe: TechnicalChartTimeframe): boolean {
  return timeframe.smart_money?.limitations?.includes(DATE_LABEL_TIME_LIMITATION) ?? false;
}

export type ChartStructure = { id: string; kind: string; occurred_at: string; confirmed_at: string; status: string; values: string; };
export function chartStructures(timeframe: TechnicalChartTimeframe, cutoff?: string): ChartStructure[] {
  const smc = timeframe.smart_money;
  if (!smc) return [];
  if (cutoff && hasDateLabelTimes(timeframe)) return [];
  const rows: ChartStructure[] = [
    ...(smc.swings ?? []).map((e, i) => ({ id: `swing-${i}`, kind: `${e.scope} ${e.label}`, occurred_at: e.occurred_at, confirmed_at: e.confirmed_at, status: "confirmed", values: e.price })),
    ...smc.structure_events.map((e, i) => ({ id: `event-${i}`, kind: `${e.scope} ${e.event} ${e.direction}`, occurred_at: e.occurred_at, confirmed_at: e.occurred_at, status: "confirmed", values: e.price })),
    ...smc.liquidity_levels.map((e, i) => ({ id: `liquidity-${i}`, kind: e.kind, occurred_at: e.second_swing_at, confirmed_at: e.confirmed_at, status: e.status, values: e.price })),
    ...[...smc.value_zones, ...smc.order_blocks, ...smc.fair_value_gaps].map((e, i) => ({ id: `zone-${i}`, kind: `${e.scope} ${e.kind}`, occurred_at: e.created_at, confirmed_at: e.confirmed_at, status: e.status, values: `${e.low} – ${e.high}` })),
  ];
  return rows.filter((r) => Number.isFinite(Date.parse(r.confirmed_at)) && (!cutoff || Date.parse(r.confirmed_at) <= Date.parse(cutoff)));
}
export type ChartResearchContext = {
  version: 1; id: string; created_at: string; instrument_id: string; subject_id: string;
  snapshot_as_of: string; algorithm_version: string; smc_algorithm_version: string;
  interval: "1d" | "1w"; price_basis: string; cutoff: string | null;
  target: "thesis" | "plan"; structure: ChartStructure; note: string;
  disclosures?: ChartDisclosure[];
};
/** Optional metadata keeps older version-1 contexts readable. */
function validDisclosures(value: unknown): boolean {
  if (value === undefined) return true;
  if (!Array.isArray(value) || value.length > 32) return false;
  let length = 0;
  return value.every((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)
      || Object.keys(entry).length !== 2 || !("code" in entry) || !("message" in entry)
      || typeof entry.code !== "string" || !entry.code.trim() || entry.code.length > 160
      || typeof entry.message !== "string" || !entry.message.trim() || entry.message.length > 1000) return false;
    length += entry.code.length + entry.message.length;
    return length <= 8000;
  });
}
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const KEY = "tp:chart-research:";
function contextHasDateLabelTimes(value: ChartResearchContext): boolean {
  return value.disclosures?.some((entry) => entry.code === DATE_LABEL_TIME_LIMITATION
    || entry.code === "FUTURES_DAILY_DATE_ONLY_COMPLETION_UNKNOWN") ?? false;
}
export function stageChartResearchContext(storage: StorageLike, value: ChartResearchContext): string {
  if (!validDisclosures(value.disclosures)) throw new Error("Invalid chart disclosures.");
  if (value.cutoff && contextHasDateLabelTimes(value)) throw new Error("Historical cutoff is unavailable for source date labels.");
  storage.setItem(KEY + value.id, JSON.stringify(value));
  return `/research?subject_id=${encodeURIComponent(value.subject_id)}&instrument_id=${encodeURIComponent(value.instrument_id)}&chart_context=${encodeURIComponent(value.id)}`;
}
/** Reads without deleting: remove only after the owner accepts the prefill. Never confirms a write. */
export function readChartResearchContext(storage: StorageLike, id: string, instrumentId: string, subjectId: string): ChartResearchContext | null {
  try {
    const value = JSON.parse(storage.getItem(KEY + id) ?? "null") as ChartResearchContext | null;
    if (!value || value.version !== 1 || value.id !== id || value.instrument_id !== instrumentId || value.subject_id !== subjectId
      || !["thesis", "plan"].includes(value.target) || !["1d", "1w"].includes(value.interval)
      || !value.algorithm_version || !value.smc_algorithm_version || !value.price_basis
      || !Number.isFinite(Date.parse(value.snapshot_as_of)) || !Number.isFinite(Date.parse(value.created_at))
      || Date.now() - Date.parse(value.created_at) > 24 * 60 * 60 * 1000 || Date.parse(value.created_at) > Date.now() + 60_000
      || typeof value.note !== "string" || value.note.length > 8000 || !value.structure
      || !validDisclosures(value.disclosures)
      || (value.cutoff !== null && contextHasDateLabelTimes(value))
      || !["id", "kind", "occurred_at", "confirmed_at", "status", "values"].every((key) => typeof (value.structure as unknown as Record<string, unknown>)[key] === "string")
      || !Number.isFinite(Date.parse(value.structure.confirmed_at))
      || (value.cutoff !== null && (!Number.isFinite(Date.parse(value.cutoff)) || Date.parse(value.structure.confirmed_at) > Date.parse(value.cutoff)))) return null;
    return value;
  } catch { return null; }
}
export function consumeChartResearchContext(storage: StorageLike, id: string, instrumentId: string, subjectId: string): ChartResearchContext | null {
  const value = readChartResearchContext(storage, id, instrumentId, subjectId);
  if (value) storage.removeItem(KEY + id);
  return value;
}
export function chartSceneMatches(scene: InteractiveTechnicalScene, instrumentId: string, interval: string, basis?: string): boolean {
  return scene.instrument_id === instrumentId && scene.bars_interval === interval && (!basis || scene.price_basis === basis)
    && scene.timeframes.some((frame) => frame.interval === interval) && scene.bars.length > 0;
}

export function chartResearchProvenance(value: ChartResearchContext): string {
  const dateLabels = contextHasDateLabelTimes(value);
  if (value.cutoff && dateLabels) throw new Error("Historical cutoff is unavailable for source date labels.");
  return [
    "Chart context — derived evidence for user review, not a confirmed judgment or trading instruction.",
    `Instrument: ${value.instrument_id}; Subject: ${value.subject_id}`,
    `Snapshot: ${value.snapshot_as_of}; Algorithms: ${value.algorithm_version} / ${value.smc_algorithm_version}`,
    `Source interval: ${value.interval}; Adjustment basis: ${value.price_basis}; Cutoff: ${value.cutoff ?? "snapshot"}`,
    `Structure: ${value.structure.kind}; Values: ${value.structure.values}`,
    dateLabels
      ? `Occurrence label: ${value.structure.occurred_at}; Confirmation label: ${value.structure.confirmed_at}; Status at snapshot: ${value.structure.status}. These labels do not establish observation or confirmation availability times.`
      : `Occurred: ${value.structure.occurred_at}; Confirmed: ${value.structure.confirmed_at}; Status at snapshot: ${value.structure.status}`,
    "Historical invalidation/mitigation/sweep status is not reconstructed.",
    ...(value.disclosures?.length ? ["Chart Caveats:", ...value.disclosures.map((entry) => `${entry.message} [${entry.code}]`)] : []),
  ].join("\n");
}
