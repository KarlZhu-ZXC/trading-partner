import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import ts from "typescript";

const compiled = ts.transpileModule(readFileSync(new URL("../app/research/valuation-ledger.tsx", import.meta.url), "utf8"), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const source = { instrument_id: "equity:US:TEST", source: "SEC", eps_diluted: "2", currency: "USD", unit: "USD/shares", share_basis: "as_reported", period_start: "2025-01-01", period_end: "2025-12-31", filed_at: "2026-02-01", accession: "fixture", filing_form: "10-K", as_of: "2026-09-14T00:00:00Z", shares_outstanding: null, shares_period_end: null, warning_codes: [] };
const assumptions = { normalization_factor: "1.1", normalization_step: "0.1", pe_multiple: "15", pe_step: "2", business_model: "operating_company", rationale: "Synthetic user scenario" };
const snapshot = { method: "EPS_PE", normalized_eps: "2.2", per_share_value: "33", sensitivity: [], value_basis: "as_reported", enterprise_value: null, aggregate_equity_value: null, source, assumptions, warnings: [] };
const receipt = { source_token: "token", source, expires_at: "2026-09-15T00:00:00Z" };
const version = { version_id: "version-1", supersedes_version_id: null, created_at: "2026-09-14T00:00:00Z", snapshot };
const approval = "I reviewed these source facts, assumptions and scenarios and authorize saving this version.";

function harness({ post, history = [] } = {}) {
  let cursor = 0; const slots = []; let pending = []; const calls = [];
  const react = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }]; },
    useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index]; },
    useEffect(effect, deps) { const index = cursor++; const old = slots[index]; if (!old || deps.some((dep, i) => dep !== old.deps[i])) pending.push(() => { old?.cleanup?.(); slots[index] = { deps, cleanup: effect() }; }); },
  };
  const exports = {}; const jsx = (type, props, key) => ({ type, props: props ?? {}, key });
  runInNewContext(compiled, { exports, AbortController, URLSearchParams, crypto: { randomUUID: () => `key-${calls.length}` }, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "Fragment" };
    if (name === "../components/ui") return new Proxy({ formatDate: (value) => value }, { get: (target, key) => target[key] ?? key });
    if (name === "../lib/api") return { getJson: async () => ({ data: { items: history, version_identity: "opaque" } }), sendJsonMethod: async (url, method, body, signal) => { calls.push({ url, body, signal }); return post ? post(url, body) : { data: url.endsWith("/source") ? receipt : url.endsWith("/calculate") ? snapshot : version }; } };
    throw new Error(name);
  } });
  const outer = exports.ValuationLedger({ subjectId: "subject-1", instrumentId: source.instrument_id }); let tree;
  function render() { cursor = 0; tree = outer.type(outer.props); const effects = pending; pending = []; effects.forEach((effect) => effect()); return tree; }
  function all(node) { if (Array.isArray(node)) return node.flatMap((item) => all(item)); if (!node || typeof node !== "object") return []; return [node, ...all(node.props?.children)]; }
  function button(label) { const node = all(tree).find((node) => node.type === "Button" && node.props.children === label); assert.ok(node, label); return node.props; }
  function field(label) { const node = all(tree).find((node) => node.type === "FormField" && node.props.label === label); assert.ok(node, label); return node.props.children.props; }
  async function flush() { await new Promise((resolve) => setImmediate(resolve)); render(); }
  function unmount() { for (const slot of slots) slot?.cleanup?.(); }
  function edit(label, value) { field(label).onChange({ target: { value } }); render(); }
  function authorize() { edit("Save Authorization Note", "Save synthetic assumptions"); field(approval).onChange({ target: { checked: true } }); render(); }
  render(); return { render, button, field, flush, calls, unmount, exports, edit, authorize };
}
async function prepare(h) {
  await h.flush(); h.button("Retrieve SEC Annual EPS").onClick(); await h.flush();
  for (const [label, value] of [["Business Model", "operating_company"], ["EPS Normalization Factor", "1.1"], ["Normalization Sensitivity Step", "0.1"], ["P/E Multiple", "15"], ["P/E Sensitivity Step", "2"], ["Assumption Rationale", "Synthetic user scenario"]]) h.edit(label, value);
  h.button("Calculate Scenarios").onClick(); await h.flush();
}

test("source retrieval is explicit; numeric defaults are empty and unsupported models cannot calculate", async () => {
  const h = harness(); await h.flush(); assert.equal(h.calls.length, 0);
  for (const label of ["Business Model", "EPS Normalization Factor", "Normalization Sensitivity Step", "P/E Multiple", "P/E Sensitivity Step", "Assumption Rationale"]) { assert.equal(h.field(label).value, ""); assert.equal(h.field(label).required, true); }
  assert.equal(h.button("Calculate Scenarios").disabled, true);
  h.button("Retrieve SEC Annual EPS").onClick(); await h.flush(); h.edit("Business Model", "bank");
  assert.equal(h.button("Calculate Scenarios").disabled, true);
});
test("calculation sends string assumptions; editing clears preview and authorization but retains source", async () => {
  const h = harness(); await prepare(h);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.find((call) => call.url.endsWith("/calculate")).body.assumptions)), assumptions);
  h.authorize(); assert.equal(h.button("Save Valuation Version").disabled, false);
  h.edit("P/E Multiple", "16"); assert.equal(h.button("Save Valuation Version").disabled, true);
  assert.equal(h.field("Save Authorization Note").value, ""); assert.equal(h.button("Calculate Scenarios").disabled, false);
  assert.equal(h.calls.filter((call) => call.url.endsWith("/source")).length, 1);
});
test("failed save retries reuse the idempotency key for the same exact payload", async () => {
  const h = harness({ post(url) { if (url.endsWith("/versions")) throw new Error("Connection interrupted"); return { data: url.endsWith("/source") ? receipt : snapshot }; } });
  await prepare(h); h.authorize(); h.button("Save Valuation Version").onClick(); await h.flush(); h.button("Save Valuation Version").onClick(); await h.flush();
  const saves = h.calls.filter((call) => call.url.endsWith("/versions")); assert.equal(saves.length, 2);
  assert.equal(saves[0].body.idempotency_key, saves[1].body.idempotency_key); assert.equal(saves[0].body.confirmed, true); assert.equal(saves[0].body.supersedes_version_id, null);
});
test("restore pins exact prior version and requires explicit source retrieval again", async () => {
  const h = harness({ history: [version] }); await prepare(h); h.button("Restore Assumptions From This Version").onClick(); h.render();
  assert.equal(h.field("P/E Multiple").value, "15"); assert.equal(h.button("Calculate Scenarios").disabled, true);
  h.button("Retrieve SEC Annual EPS").onClick(); await h.flush(); h.button("Calculate Scenarios").onClick(); await h.flush(); h.authorize();
  h.button("Save Valuation Version").onClick(); await h.flush(); assert.equal(h.calls.at(-1).body.supersedes_version_id, "version-1");
});
test("subject changes reset keyed state and unmount ignores late source responses", async () => {
  let resolve; const h = harness({ post: () => new Promise((done) => { resolve = done; }) }); await h.flush();
  assert.notEqual(h.exports.ValuationLedger({ subjectId: "one", instrumentId: source.instrument_id }).key, h.exports.ValuationLedger({ subjectId: "two", instrumentId: source.instrument_id }).key);
  h.button("Retrieve SEC Annual EPS").onClick(); h.unmount(); assert.equal(h.calls[0].signal.aborted, true);
  resolve({ data: receipt }); await h.flush(); assert.equal(h.button("Calculate Scenarios").disabled, true);
});
