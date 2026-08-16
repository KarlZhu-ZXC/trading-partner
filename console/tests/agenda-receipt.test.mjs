import assert from "node:assert/strict";
import test from "node:test";
import { unwrapAgendaSync } from "../app/lib/agenda-receipt.mjs";

const RECEIPT = {
  receipt_id: "agenda_sync_0192",
  status: "COMPLETED",
  as_of: "2026-08-16T00:00:00Z",
  window_start: "2026-08-16T00:00:00Z",
  window_end: "2026-09-16T00:00:00Z",
  scope_count: 3,
  eligible_instrument_count: 2,
  succeeded_scope_count: 2,
  failed_scope_count: 1,
  candidate_count: 5,
  appended_count: 4,
  revised_count: 1,
  date_drift_count: 0,
  unchanged_count: 7,
  provider_results: [
    {
      vendor: "yahoo",
      scope_ref: "equity:US:SGOV",
      status: "FAILED",
      candidate_count: 0,
      error_code: "UPSTREAM_RATE_LIMITED",
      warning_codes: ["PROVIDER_FALLBACK_USED"],
    },
  ],
  limitation_codes: ["PARTIAL_PROVIDER_COVERAGE", "WINDOW_TRUNCATED"],
  started_at: "2026-08-16T09:00:00Z",
  completed_at: "2026-08-16T09:00:42Z",
  schema_version: 2,
  execution_effect: false,
};

test("unwrapAgendaSync keeps envelope-level limitation codes", () => {
  // Regression: the historical page code passed the raw array through
  // listOf(value, "a"), silently dropping every limitation code and hiding
  // sync warnings from the user.
  const receipt = unwrapAgendaSync(RECEIPT);
  assert.notEqual(receipt, null);
  assert.deepEqual(receipt.limitation_codes, [
    "PARTIAL_PROVIDER_COVERAGE",
    "WINDOW_TRUNCATED",
  ]);
});

test("unwrapAgendaSync accepts bare, data-wrapped, and result-wrapped envelopes", () => {
  assert.equal(unwrapAgendaSync(RECEIPT).receipt_id, "agenda_sync_0192");
  assert.equal(unwrapAgendaSync({ data: RECEIPT }).receipt_id, "agenda_sync_0192");
  assert.equal(unwrapAgendaSync({ result: RECEIPT }).receipt_id, "agenda_sync_0192");
});

test("unwrapAgendaSync prefers source, then data, then result", () => {
  const layered = unwrapAgendaSync({
    status: "OUTER",
    data: { ...RECEIPT, status: "INNER" },
    result: { status: "RESULT" },
  });
  assert.equal(layered.status, "OUTER");
  const innerOnly = unwrapAgendaSync({
    data: { ...RECEIPT, status: "INNER" },
    result: { status: "RESULT" },
  });
  assert.equal(innerOnly.status, "INNER");
});

test("unwrapAgendaSync normalizes provider rows and count fields", () => {
  const receipt = unwrapAgendaSync({ data: RECEIPT });
  assert.equal(receipt.succeeded_scope_count, 2);
  assert.equal(receipt.failed_scope_count, 1);
  assert.equal(receipt.provider_results.length, 1);
  assert.equal(receipt.provider_results[0].error_code, "UPSTREAM_RATE_LIMITED");
  assert.deepEqual(receipt.provider_results[0].warning_codes, ["PROVIDER_FALLBACK_USED"]);
});

test("unwrapAgendaSync tolerates missing or malformed fields", () => {
  const sparse = unwrapAgendaSync({ data: { receipt_id: "agenda_sync_0001" } });
  assert.equal(sparse.receipt_id, "agenda_sync_0001");
  assert.equal(sparse.scope_count, 0);
  assert.deepEqual(sparse.limitation_codes, []);
  assert.deepEqual(sparse.provider_results, []);
  assert.equal(sparse.execution_effect, false);

  const malformed = unwrapAgendaSync({
    data: {
      receipt_id: "agenda_sync_0002",
      limitation_codes: "not-an-array",
      provider_results: [{ scope_ref: 7 }, null, "junk"],
    },
  });
  assert.deepEqual(malformed.limitation_codes, []);
  assert.equal(malformed.provider_results.length, 1);
  assert.equal(malformed.provider_results[0].vendor, "");
});

test("unwrapAgendaSync returns null when no receipt_id survives", () => {
  assert.equal(unwrapAgendaSync(null), null);
  assert.equal(unwrapAgendaSync({}), null);
  assert.equal(unwrapAgendaSync({ data: { status: "COMPLETED" } }), null);
  assert.equal(unwrapAgendaSync("junk"), null);
});
