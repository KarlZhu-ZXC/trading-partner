import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { syntheticTechnicalChartScene } from "../dev/chart-fixtures.ts";
import { chartWarningDisclosures, smcDisclosures } from "../app/lib/technical-disclosures.ts";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const cache = new Map();
// Render the real TSX component; only CSS modules are inert in this headless
// test. Chart effects are intentionally left for Playwright's browser checks.
function loadTsx(filename) {
  if (filename.endsWith(".css")) return { __esModule: true, default: {} };
  if (cache.has(filename)) return cache.get(filename).exports;
  const module = { exports: {} };
  cache.set(filename, module);
  const compiled = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const localRequire = (specifier) => {
    if (!specifier.startsWith(".")) return require(specifier);
    const base = path.resolve(path.dirname(filename), specifier);
    const resolved = [base, `${base}.ts`, `${base}.tsx`].find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
    return loadTsx(resolved);
  };
  new Function("require", "module", "exports", compiled)(localRequire, module, module.exports);
  return module.exports;
}
const { InteractiveTechnicalChart } = loadTsx(fileURLToPath(new URL("../app/components/interactive-technical-chart.tsx", import.meta.url)));

test("native-date futures chart disables historical cutoff and retains snapshot structures", () => {
  const scene = syntheticTechnicalChartScene();
  scene.instrument_id = "future:US:SI=F";
  scene.timeframes[0].smart_money.limitations = ["SMC_FUTURES_DATE_LABEL_CONFIRMATION_TIME_UNKNOWN"];
  const html = renderToStaticMarkup(React.createElement(InteractiveTechnicalChart, { scene }));
  assert.match(html, /type="datetime-local"[^>]*disabled=""/);
  assert.match(html, /not verified availability times/);
  assert.match(html, /Historical cutoff filtering is unavailable/);
  assert.match(html, /<option value="event-0">/);
  assert.match(html, /260 bars/);
});

test("long weekly chart renders every limitation even when ATR200 is ready", () => {
  const scene = syntheticTechnicalChartScene();
  scene.bars_interval = "1w";
  scene.bars = scene.bars.map((bar, index) => ({ ...bar, timestamp: new Date(Date.UTC(2021, 0, 4, 21) + index * 7 * 86400000).toISOString() }));
  scene.timeframes[0].interval = "1w";
  scene.timeframes[0].smart_money.limitations = ["SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL", "SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE", "SMC_ADDITIONAL_SOURCE_LIMITATION"];
  assert.equal(scene.bars.length, 260);
  for (const ready of [false, true]) {
    scene.timeframes[0].smart_money.atr_200_ready = ready;
    const html = renderToStaticMarkup(React.createElement(InteractiveTechnicalChart, { scene }));
    assert.match(html, /SMC Limitations/);
    assert.match(html, /structures involving an unfinished week remain provisional/);
    assert.match(html, /Percentage-based fair-value gaps are unavailable for those candles/);
    assert.match(html, /An additional SMC limitation applies/);
    for (const code of scene.timeframes[0].smart_money.limitations) assert.ok(html.includes(code));
  }
});

test("daily chart without limitations keeps its disclosed price basis and no extra limitation notice", () => {
  const scene = syntheticTechnicalChartScene();
  const html = renderToStaticMarkup(React.createElement(InteractiveTechnicalChart, { scene }));
  assert.match(html, /split and dividend adjusted daily close/);
  assert.match(html, /Snapshot SMC bullish/);
  assert.doesNotMatch(html, /aria-label="SMC Limitations"/);
});

test("SMC guidance retains ATR history limitations and deduplicates codes", () => {
  const code = "SMC_ATR_200_UNAVAILABLE_ORDER_BLOCK_FILTER_AND_EQH_EQL";
  const notes = smcDisclosures([code, ` ${code} `, ""]);
  assert.equal(notes.length, 1);
  assert.match(notes[0].message, /History is too short/);
  assert.match(notes[0].message, /equal-high\/low levels are limited/);
});

test("source warnings preserve session, boundary, price basis and history guidance", () => {
  const codes = ["US_NON_SESSION_DAILY_BAR", "TECHNICAL_WEEKLY_BOUNDARIES_MAY_BE_PARTIAL", "FUTURES_CONTRACT_NOT_SPOT", "TECHNICAL_HISTORY_INCOMPLETE", "VOLUME_BEST_BID_ASK_NOT_EXCHANGE"];
  const notes = chartWarningDisclosures(codes.map((code) => ({ code, message: "Provider supplied this warning code." })));
  assert.deepEqual(notes.map((note) => note.code), codes);
  for (const phrase of ["outside exchange sessions", "weeks may be incomplete", "futures contract prices", "remain unavailable", "broker quote-side activity"]) assert.ok(notes.some((note) => note.message.includes(phrase)));
  assert.ok(notes.every((note) => !note.message.includes("Provider supplied")));
});

test("unknown warnings retain human source messages or fallback and secondary source codes", () => {
  const notes = chartWarningDisclosures([
    { code: "SOURCE_COVERAGE_LIMIT", message: "Only part of the requested history is available." },
    { code: "SOURCE_COVERAGE_LIMIT", message: "Repeated message" },
    { code: "OTHER_SOURCE_LIMIT", message: "Provider supplied this warning code." },
  ]);
  assert.equal(notes.length, 2);
  assert.equal(notes[0].message, "Only part of the requested history is available.");
  assert.match(notes[1].message, /A source limitation applies/);
  assert.equal(notes[1].code, "OTHER_SOURCE_LIMIT");
});


test("futures chart caveats distinguish a conservative bound from unknown completion", () => {
  const notes = chartWarningDisclosures([
    { code: "FUTURES_DAILY_CONSERVATIVE_COMPLETION_BOUND", message: "Provider supplied this warning code." },
    { code: "FUTURES_DAILY_DATE_ONLY_COMPLETION_UNKNOWN", message: "Provider supplied this warning code." },
  ]);
  assert.match(notes[0].message, /17:00 New York availability bound/);
  assert.match(notes[0].message, /not a proven last-trade time/);
  assert.match(notes[0].message, /actual holiday close/);
  assert.match(notes[1].message, /native New York date labels/);
  assert.match(notes[1].message, /next local date/);
  assert.match(notes[1].message, /completion time are unknown/);
});
