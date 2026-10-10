import assert from "node:assert/strict";
import test from "node:test";
import { asRecord, asObject, displayDate, textDash, textStrict, textTrimmed, textStringified, numberOrZero } from "../app/lib/coerce.ts";

test("surface text conventions preserve missing, zero and whitespace distinctions", () => {
  const cases = [
    [null, "—", "", "—", "—"],
    [undefined, "—", "", "—", "—"],
    ["", "—", "", "—", "—"],
    ["  ", "—", "", "—", "  "],
    ["  Note  ", "Note", "  Note  ", "Note", "  Note  "],
    [0, "0", "", "—", "0"],
    [false, "—", "", "—", "false"],
    [Infinity, "—", "", "—", "Infinity"],
    [NaN, "—", "", "—", "NaN"],
    [[], "—", "", "—", ""],
    [{}, "—", "", "—", "[object Object]"],
  ];
  for (const [input, ...expected] of cases) {
    assert.deepEqual([textDash(input), textStrict(input), textTrimmed(input), textStringified(input)], expected);
  }
  for (const fn of [textDash, textStrict, textTrimmed, textStringified]) {
    assert.equal(fn(null, "missing"), "missing");
  }
});

test("record guard excludes arrays without cloning accepted objects", () => {
  for (const input of [null, undefined, [], "text", 0, false]) assert.deepEqual(asRecord(input), {});
  const record = { amount: null };
  assert.equal(asRecord(record), record);
  assert.equal(asObject(record), record);
  const array = [record];
  assert.equal(asObject(array), array);
  for (const input of [null, undefined, "text", 0, false]) assert.deepEqual(asObject(input), {});
});

test("Agent dates preserve unavailable and invalid values and use viewer timezone", () => {
  for (const input of [null, undefined, ""]) assert.equal(displayDate(input), "—");
  assert.equal(displayDate("invalid source date"), "invalid source date");
  const value = "2026-09-16T00:10:00Z";
  const parts = new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value));
  assert.equal(displayDate(value), parts);
});

test("Journal numeric fallback preserves existing coercion without claiming known values", () => {
  for (const input of [null, undefined, "", "bad", NaN, Infinity, -Infinity, [], {}]) {
    assert.equal(numberOrZero(input), 0);
  }
  assert.equal(numberOrZero(" 12.5 "), 12.5);
  assert.equal(numberOrZero(-3), -3);
  assert.equal(numberOrZero(true), 1);
  assert.equal(Object.is(numberOrZero(-0), -0), true);
});
