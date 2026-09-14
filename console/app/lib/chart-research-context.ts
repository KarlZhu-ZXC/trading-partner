import type { InteractiveTechnicalScene, TechnicalChartTimeframe } from "../components/interactive-technical-chart";

export type ChartStructure = { id: string; kind: string; occurred_at: string; confirmed_at: string; status: string; values: string; };
export function chartStructures(timeframe: TechnicalChartTimeframe, cutoff?: string): ChartStructure[] {
  const smc = timeframe.smart_money;
  if (!smc) return [];
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
};
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const KEY = "tp:chart-research:";
export function stageChartResearchContext(storage: StorageLike, value: ChartResearchContext): string {
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
  return [
    "Chart context — derived evidence for user review, not a confirmed judgment or trading instruction.",
    `Instrument: ${value.instrument_id}; Subject: ${value.subject_id}`,
    `Snapshot: ${value.snapshot_as_of}; Algorithms: ${value.algorithm_version} / ${value.smc_algorithm_version}`,
    `Source interval: ${value.interval}; Adjustment basis: ${value.price_basis}; Cutoff: ${value.cutoff ?? "snapshot"}`,
    `Structure: ${value.structure.kind}; Values: ${value.structure.values}`,
    `Occurred: ${value.structure.occurred_at}; Confirmed: ${value.structure.confirmed_at}; Status at snapshot: ${value.structure.status}`,
    "Historical invalidation/mitigation/sweep status is not reconstructed.",
  ].join("\n");
}
