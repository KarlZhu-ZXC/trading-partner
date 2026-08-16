import assert from "node:assert/strict";
import test from "node:test";
import { endOfDayIsoOrNull } from "../app/lib/review-due-date.mjs";

test("endOfDayIsoOrNull converts a valid YYYY-MM-DD to local end-of-day", () => {
  // The timestamp is the local end of the requested day, matching the
  // historical page behaviour; compare against the same local construction
  // so the test is timezone-independent.
  assert.equal(
    endOfDayIsoOrNull("2026-08-16"),
    new Date(2026, 7, 16, 23, 59, 59, 0).toISOString(),
  );
});

test("endOfDayIsoOrNull trims surrounding whitespace", () => {
  assert.equal(
    endOfDayIsoOrNull("  2026-12-31  "),
    new Date(2026, 11, 31, 23, 59, 59, 0).toISOString(),
  );
});

test("endOfDayIsoOrNull rejects malformed input instead of throwing", () => {
  // Regression: the page code called new Date(...).toISOString() before its
  // NaN guard, so malformed input raised RangeError instead of validating.
  assert.equal(endOfDayIsoOrNull("not-a-date"), null);
  assert.equal(endOfDayIsoOrNull("2026/08/16"), null);
  assert.equal(endOfDayIsoOrNull("16-08-2026"), null);
  assert.equal(endOfDayIsoOrNull("2026-8-16"), null);
});

test("endOfDayIsoOrNull rejects calendar-invalid dates without rollover", () => {
  // Date would silently roll these forward to the next valid day.
  assert.equal(endOfDayIsoOrNull("2026-13-01"), null);
  assert.equal(endOfDayIsoOrNull("2026-02-30"), null);
  assert.equal(endOfDayIsoOrNull("2026-00-10"), null);
  assert.equal(endOfDayIsoOrNull("2026-04-00"), null);
});

test("endOfDayIsoOrNull treats empty input as absent", () => {
  assert.equal(endOfDayIsoOrNull(""), null);
  assert.equal(endOfDayIsoOrNull("   "), null);
  assert.equal(endOfDayIsoOrNull(null), null);
  assert.equal(endOfDayIsoOrNull(undefined), null);
});
