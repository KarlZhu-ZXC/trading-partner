import assert from "node:assert/strict";
import test from "node:test";
import { hasDateLabelTimes } from "../app/lib/chart-research-context.ts";
import { chartStructures, chartSceneMatches, chartResearchProvenance, stageChartResearchContext, readChartResearchContext, consumeChartResearchContext } from "../app/lib/chart-research-context.ts";
const frame = { interval: "1d", smart_money: {
  structure_events: [{ scope: "swing", event: "bos", direction: "bullish", occurred_at: "2026-09-10T00:00:00Z", price: "100" }],
  liquidity_levels: [{ kind: "equal_high", second_swing_at: "2026-09-08T00:00:00Z", confirmed_at: "2026-09-12T00:00:00Z", status: "swept", price: "101" }],
  value_zones: [], fair_value_gaps: [], order_blocks: [{ kind: "order_block", scope: "swing", created_at: "2026-09-08T00:00:00Z", confirmed_at: "2026-09-11T00:00:00Z", status: "invalidated", low: "99", high: "100" }],
} };

test("Native daily date labels cannot establish a historical confirmation cutoff", () => {
  const limitation = "SMC_FUTURES_DATE_LABEL_CONFIRMATION_TIME_UNKNOWN";
  const dateFrame = { interval: "1d", smart_money: {
    limitations: [limitation], structure_events: [], liquidity_levels: [], value_zones: [],
    order_blocks: [], fair_value_gaps: [], swings: [{ scope: "internal", label: "LL", price: "90",
      occurred_at: "2026-07-17T00:00:00-04:00", confirmed_at: "2026-07-24T00:00:00-04:00" }],
  } };
  // The July24 row is admitted by the provider only on July25; its midnight
  // native label must not make this later snapshot's swing visible at July24 noon.
  assert.equal(hasDateLabelTimes(dateFrame), true);
  assert.equal(chartStructures(dateFrame).length, 1);
  assert.deepEqual(chartStructures(dateFrame, "2026-07-24T12:00:00-04:00"), []);
  assert.deepEqual(chartStructures({ ...dateFrame, interval: "1w" }, "2026-07-24T12:00:00-04:00"), []);
  assert.equal(hasDateLabelTimes(frame), false);
});

test("Date-label snapshot handoff remains usable but cannot assert a historical cutoff", () => {
  for (const code of ["SMC_FUTURES_DATE_LABEL_CONFIRMATION_TIME_UNKNOWN", "FUTURES_DAILY_DATE_ONLY_COMPLETION_UNKNOWN"]) {
    const storage = contextStorage();
    const current = { ...reviewedContext(), cutoff: null, disclosures: [{ code, message: "Date labels are not availability times." }] };
    const past = { ...current, cutoff: "2026-09-11T00:00:00Z" };
    stageChartResearchContext(storage, current);
    assert.ok(readChartResearchContext(storage, current.id, current.instrument_id, current.subject_id));
    assert.match(chartResearchProvenance(current), /Confirmation label:/);
    assert.match(chartResearchProvenance(current), /do not establish observation or confirmation availability times/);
    assert.throws(() => stageChartResearchContext(storage, past), /Historical cutoff is unavailable/);
    assert.throws(() => chartResearchProvenance(past), /Historical cutoff is unavailable/);
    storage.setItem("tp:chart-research:" + past.id, JSON.stringify(past));
    assert.equal(readChartResearchContext(storage, past.id, past.instrument_id, past.subject_id), null);
  }
});
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

function contextStorage() {
  const map = new Map();
  return { getItem: (key) => map.get(key) ?? null, setItem: (key, value) => map.set(key, value), removeItem: (key) => map.delete(key) };
}
function reviewedContext() {
  return { version: 1, id: "with-caveats", created_at: new Date().toISOString(), instrument_id: "equity:US:TEST", subject_id: "case_fixture", snapshot_as_of: "2026-09-14T00:00:00Z", algorithm_version: "tp_technical_v3", smc_algorithm_version: "tp_smc_v1", interval: "1w", price_basis: "adjusted", cutoff: null, target: "thesis", structure: chartStructures(frame)[0], note: "My original interpretation" };
}
test("Mapped chart caveats survive staging, exact binding and draft provenance without changing structure or note", () => {
  const storage = contextStorage();
  const value = { ...reviewedContext(), disclosures: [
    { code: "SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL", message: "Unfinished weekly structures remain provisional." },
    { code: "SOURCE_COVERAGE_LIMIT", message: "Only part of the requested history is available." },
  ] };
  const url = stageChartResearchContext(storage, value);
  assert.doesNotMatch(url, /provisional|COVERAGE|interpretation/);
  assert.equal(readChartResearchContext(storage, value.id, value.instrument_id, "wrong"), null);
  const read = readChartResearchContext(storage, value.id, value.instrument_id, value.subject_id);
  assert.deepEqual(read, value);
  const text = chartResearchProvenance(read);
  assert.match(text, /provisional/);
  assert.match(text, /Only part of the requested history is available/);
  assert.match(text, /Status at snapshot: confirmed/);
  assert.match(text, /\[SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL\]/);
  assert.ok(consumeChartResearchContext(storage, value.id, value.instrument_id, value.subject_id));
  assert.equal(readChartResearchContext(storage, value.id, value.instrument_id, value.subject_id), null);
});
test("Optional version-1 disclosure metadata has a closed, bounded shape while legacy contexts remain readable", () => {
  const storage = contextStorage();
  const value = reviewedContext();
  stageChartResearchContext(storage, value);
  assert.deepEqual(readChartResearchContext(storage, value.id, value.instrument_id, value.subject_id), value);
  const valid = { code: "SOURCE_LIMIT", message: "Use the available source with care." };
  const invalid = [null, {}, "warning", [null], [{ ...valid, extra: true }], [{ code: " ", message: "Warning" }], [{ code: "C".repeat(161), message: "Warning" }], [{ ...valid, message: " " }], [{ ...valid, message: "m".repeat(1001) }], Array(33).fill(valid), Array(9).fill({ code: "SOURCE_LIMIT", message: "m".repeat(900) })];
  for (const disclosures of invalid) {
    const malformed = { ...value, disclosures };
    assert.throws(() => stageChartResearchContext(storage, malformed), /Invalid chart disclosures/);
    // Session storage is untrusted: the same guard also applies on read.
    storage.setItem(`tp:chart-research:${value.id}`, JSON.stringify(malformed));
    assert.equal(readChartResearchContext(storage, value.id, value.instrument_id, value.subject_id), null);
  }
  stageChartResearchContext(storage, { ...value, disclosures: [] });
  assert.deepEqual(readChartResearchContext(storage, value.id, value.instrument_id, value.subject_id).disclosures, []);
});
