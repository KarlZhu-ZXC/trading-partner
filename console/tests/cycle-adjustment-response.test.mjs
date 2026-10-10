import assert from "node:assert/strict";
import test from "node:test";
import { requireCycleOverridePreview, requireCycleOverrideReceipt } from "../app/decision-workbench/cycle-adjustment-response.ts";
import { syntheticCycleOverridePreview, syntheticCycleOverrideReceipt } from "../dev/trade-cycle-override-fixtures.ts";

const request = {
  root_cycle_id: "cycle_synthetic", operation: "SPLIT", cycle_ids: ["cycle_synthetic"],
  activity_ids: [], split_groups: [["fill_one"], ["fill_two"]], target_cycle_id: null,
  expected_version: 2, idempotency_key: "synthetic-original-request",
  note: null, authorization_note: "Synthetic explicit review",
};

test("an exact applied DTO receipt verifies the original version and key on repeat", () => {
  const receipt = syntheticCycleOverrideReceipt(request);
  assert.equal(requireCycleOverrideReceipt(receipt, request), receipt);
  assert.equal(requireCycleOverrideReceipt(structuredClone(receipt), request).version, 3);
});

for (const [name, value] of [
  ["null", null], ["empty", {}], ["array", []], ["old minimal mock", { version: 3 }],
  ["foreign root", { root_cycle_id: "cycle_foreign" }], ["foreign operation", { operation: "MERGE" }],
  ["foreign cycles", { cycle_ids: ["cycle_foreign"] }], ["foreign activities", { activity_ids: ["foreign_fill"] }],
  ["foreign split groups", { split_groups: [["fill_two"], ["fill_one"]] }], ["foreign target", { target_cycle_id: "cycle_foreign" }],
  ["foreign key", { idempotency_key: "different-key" }], ["wrong version", { version: 4 }],
  ["string version", { version: "3" }], ["empty identity", { override_id: " " }],
  ["preview identity", { override_id: "trade_cycle_override_preview" }],
]) {
  test(`applied receipt rejects ${name} without mutating the reviewed request`, () => {
    const original = structuredClone(request);
    const receipt = name.startsWith("foreign") || ["wrong version", "string version", "empty identity", "preview identity"].includes(name)
      ? syntheticCycleOverrideReceipt(request, value) : value;
    assert.throws(() => requireCycleOverrideReceipt(receipt, request), /original request key are retained/);
    assert.deepEqual(request, original);
  });
}

for (const operation of ["SPLIT", "MERGE", "RELINK"]) {
  test(`${operation} preview validates the proposed revision and usable effective projection`, () => {
    const proposed = operation === "SPLIT" ? request : {
      ...request, operation, cycle_ids: [request.root_cycle_id, "cycle_second"], split_groups: [],
      activity_ids: operation === "RELINK" ? ["fill_move"] : [],
      target_cycle_id: operation === "RELINK" ? "cycle_second" : null,
    };
    const preview = syntheticCycleOverridePreview(proposed);
    assert.equal(requireCycleOverridePreview(preview, proposed), preview);
  });
}

const corruptions = [
  ["missing proposed revision", (p) => { p.applied_revisions = []; }],
  ["duplicate proposed revision", (p) => { p.applied_revisions.push(p.applied_revisions[0]); p.impacts.push(p.impacts[0]); }],
  ["foreign revision root", (p) => { p.applied_revisions[0].root_cycle_id = "foreign"; }],
  ["foreign revision key", (p) => { p.applied_revisions[0].idempotency_key = "foreign"; }],
  ["wrong revision version", (p) => { p.applied_revisions[0].version = 4; }],
  ["different split groups", (p) => { p.applied_revisions[0].split_groups = [["fill_two"], ["fill_one"]]; }],
  ["foreign impact operation", (p) => { p.impacts[0].operation = "MERGE"; }],
  ["foreign source", (p) => { p.impacts[0].source_cycle_ids = ["foreign"]; }],
  ["absent result", (p) => { p.impacts[0].result_cycle_ids = ["missing"]; }],
  ["duplicate result", (p) => { p.impacts[0].result_cycle_ids.push(p.impacts[0].result_cycle_ids[0]); }],
  ["lost moved fill", (p) => { p.impacts[0].moved_activity_ids = ["fill_one"]; }],
  ["wrong impact count", (p) => { p.impacts[0].after_activity_count = 99; }],
  ["counts inconsistent with results", (p) => { p.impacts[0].before_activity_count = 99; p.impacts[0].after_activity_count = 99; }],
  ["missing warning facts", (p) => { delete p.impacts[0].warning_codes; }],
  ["missing algorithm projection", (p) => { p.algorithm_projection.cycles = []; }],
  ["missing effective cycles", (p) => { p.effective_projection.cycles = []; }],
  ["wrong result membership", (p) => { p.effective_projection.cycles[0].activity_ids = ["foreign_fill"]; }],
  ["duplicate effective identity", (p) => { p.effective_projection.cycles.push(p.effective_projection.cycles[0]); }],
];
for (const [name, corrupt] of corruptions) {
  test(`preview rejects ${name} without mutating the request`, () => {
    const preview = syntheticCycleOverridePreview(request);
    const original = structuredClone(request);
    corrupt(preview);
    assert.throws(() => requireCycleOverridePreview(preview, request), /nothing was applied/);
    assert.deepEqual(request, original);
  });
}

test("an older matching-looking impact cannot substitute for the proposed revision", () => {
  const preview = syntheticCycleOverridePreview(request);
  preview.applied_revisions.unshift(syntheticCycleOverrideReceipt({ ...request, idempotency_key: "older-key" }));
  preview.impacts.unshift(structuredClone(preview.impacts[0]));
  assert.equal(requireCycleOverridePreview(preview, request), preview);
  preview.impacts[1].source_cycle_ids = ["foreign"];
  assert.throws(() => requireCycleOverridePreview(preview, request), /nothing was applied/);
});

for (const [name, overrides] of [
  ["changed note", { note: "A different unreviewed rationale" }],
  ["missing note", { note: undefined }],
  ["null note", { note: null }],
  ["changed authorization", { authorization_note: "Different authorization" }],
  ["missing authorization", { authorization_note: undefined }],
  ["null authorization", { authorization_note: null }],
]) {
  test(`preview and saved receipt reject ${name} without changing reviewed metadata`, () => {
    const reviewed = { ...request, note: "Reviewed rationale" };
    const original = structuredClone(reviewed);
    const receipt = syntheticCycleOverrideReceipt(reviewed, overrides);
    const preview = syntheticCycleOverridePreview(reviewed);
    Object.assign(preview.applied_revisions[0], overrides);
    assert.throws(() => requireCycleOverrideReceipt(receipt, reviewed), /original request key are retained/);
    assert.throws(() => requireCycleOverridePreview(preview, reviewed), /nothing was applied/);
    assert.deepEqual(reviewed, original);
  });
}

for (const note of [null, "Trimmed reviewed rationale", "保留审阅理由\nSecond line"]) {
  test(`exact reviewed note ${JSON.stringify(note)} remains compatible with both responses`, () => {
    const reviewed = { ...request, note };
    const receipt = syntheticCycleOverrideReceipt(reviewed);
    const preview = syntheticCycleOverridePreview(reviewed);
    assert.equal(requireCycleOverrideReceipt(receipt, reviewed), receipt);
    assert.equal(requireCycleOverridePreview(preview, reviewed), preview);
  });
}

test("an omitted receipt note cannot stand in for an explicitly reviewed null note", () => {
  const receipt = syntheticCycleOverrideReceipt(request);
  const preview = syntheticCycleOverridePreview(request);
  delete receipt.note;
  delete preview.applied_revisions[0].note;
  assert.throws(() => requireCycleOverrideReceipt(receipt, request), /original request key are retained/);
  assert.throws(() => requireCycleOverridePreview(preview, request), /nothing was applied/);
});
