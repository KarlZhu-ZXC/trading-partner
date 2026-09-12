import assert from "node:assert/strict";
import test from "node:test";
import { cycleActivityCounts } from "../app/lib/trade-cycle-counts.ts";

test("Cycle counts preserve numeric values and genuine zeroes", () => {
  assert.equal(cycleActivityCounts({ add_count: 2, reduce_count: 3 }), "2 / 3");
  assert.equal(cycleActivityCounts({ add_count: 0, reduce_count: 1 }), "0 / 1");
  assert.equal(cycleActivityCounts({ add_count: 0, reduce_count: 0 }), "0 / 0");
  assert.equal(cycleActivityCounts({ add_count: " 2 ", reduce_count: "3" }), "2 / 3");
});

test("Missing or invalid counts are not displayed as zero", () => {
  for (const value of [undefined, null, "", "unknown", -1, 1.5, NaN, Infinity, true]) {
    assert.equal(cycleActivityCounts({ add_count: value, reduce_count: 1 }), "— / 1");
  }
});

test("Manual correction placeholders remain unavailable until recalculated", () => {
  assert.equal(cycleActivityCounts({
    add_count: 0,
    reduce_count: 0,
    warning_codes: ["MANUAL_RECOMPUTE_REQUIRED"],
  }), "— / —");
  assert.equal(cycleActivityCounts({
    add_count: 0,
    reduce_count: 1,
    warning_codes: ["SELL_WITHOUT_OPEN_LONG"],
  }), "0 / 1");
});
