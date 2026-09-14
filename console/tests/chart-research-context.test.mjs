import assert from "node:assert/strict";
import test from "node:test";
import { chartStructures, chartSceneMatches, chartResearchProvenance, stageChartResearchContext, readChartResearchContext, consumeChartResearchContext } from "../app/lib/chart-research-context.ts";
const frame = { interval: "1d", smart_money: {
  structure_events: [{ scope: "swing", event: "bos", direction: "bullish", occurred_at: "2026-09-10T00:00:00Z", price: "100" }],
  liquidity_levels: [{ kind: "equal_high", second_swing_at: "2026-09-08T00:00:00Z", confirmed_at: "2026-09-12T00:00:00Z", status: "swept", price: "101" }],
  value_zones: [], fair_value_gaps: [], order_blocks: [{ kind: "order_block", scope: "swing", created_at: "2026-09-08T00:00:00Z", confirmed_at: "2026-09-11T00:00:00Z", status: "invalidated", low: "99", high: "100" }],
} };
test("Historical selection excludes structures whose confirmation came later, preserving snapshot status", () => {
  assert.equal(chartStructures(frame, "2026-09-09T00:00:00Z").length, 0);
  assert.deepEqual(chartStructures(frame, "2026-09-10T00:00:00Z").map((r) => r.id), ["event-0"]);
  assert.deepEqual(chartStructures(frame, "2026-09-11T00:00:00Z").map((r) => r.status), ["confirmed", "invalidated"]);
  assert.equal(chartStructures(frame).length, 3);
});
test("Scene binding rejects different instrument, interval, or basis", () => {
  const scene = { instrument_id: "equity:US:TEST", bars_interval: "1d", price_basis: "adjusted", bars: [{}], timeframes: [frame] };
  assert.equal(chartSceneMatches(scene, scene.instrument_id, "1d", "adjusted"), true);
  assert.equal(chartSceneMatches(scene, "equity:US:OTHER", "1d"), false);
  assert.equal(chartSceneMatches(scene, scene.instrument_id, "1w"), false);
  assert.equal(chartSceneMatches(scene, scene.instrument_id, "1d", "raw"), false);
});
test("Handoff URL excludes values and text and is bound to exact subject and instrument", () => {
  const map = new Map(); const storage = { getItem: (k) => map.get(k) ?? null, setItem: (k,v) => map.set(k,v), removeItem: (k) => map.delete(k) };
  const value = { version: 1, id: "opaque", created_at: new Date().toISOString(), instrument_id: "equity:US:TEST", subject_id: "case_fixture", snapshot_as_of: "2026-09-14T00:00:00Z", algorithm_version: "tp_technical_v3", smc_algorithm_version: "tp_smc_v1", interval: "1d", price_basis: "adjusted", cutoff: null, target: "plan", structure: chartStructures(frame)[0], note: "My private interpretation" };
  const url = stageChartResearchContext(storage, value);
  assert.doesNotMatch(url, /private|100|tp_smc/);
  assert.equal(readChartResearchContext(storage, "opaque", value.instrument_id, "wrong"), null);
  assert.equal(readChartResearchContext(storage, "opaque", "equity:US:OTHER", value.subject_id), null);
  assert.equal(readChartResearchContext(storage, "opaque", value.instrument_id, value.subject_id).note, value.note);
  assert.ok(consumeChartResearchContext(storage, "opaque", value.instrument_id, value.subject_id));
  assert.equal(readChartResearchContext(storage, "opaque", value.instrument_id, value.subject_id), null);
  stageChartResearchContext(storage, { ...value, created_at: "2000-01-01T00:00:00Z" });
  assert.equal(readChartResearchContext(storage, "opaque", value.instrument_id, value.subject_id), null);
  stageChartResearchContext(storage, { ...value, cutoff: "2026-09-09T00:00:00Z" });
  assert.equal(readChartResearchContext(storage, "opaque", value.instrument_id, value.subject_id), null);
});

test("Draft provenance retains exact chart metadata and never promotes historical status", () => {
  const text = chartResearchProvenance({ instrument_id: "equity:US:TEST", subject_id: "case_fixture", snapshot_as_of: "2026-09-14T00:00:00Z", algorithm_version: "tp_technical_v3", smc_algorithm_version: "tp_smc_v1", interval: "1w", price_basis: "adjusted", cutoff: "2026-09-11T00:00:00Z", structure: chartStructures(frame)[2] });
  for (const expected of ["equity:US:TEST", "case_fixture", "tp_technical_v3", "tp_smc_v1", "1w", "adjusted", "2026-09-11", "Status at snapshot: invalidated", "not reconstructed"]) assert.ok(text.includes(expected));
  assert.match(text, /not a confirmed judgment/);
});
