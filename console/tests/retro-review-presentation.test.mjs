import assert from "node:assert/strict";
import test from "node:test";
import { orderedFollowups, findingChoiceChanges } from "../app/lib/retro-review-presentation.ts";
test("actionable overdue work precedes conflicts and completed work without mutating source rows", () => {
  const source = [{ title: "Done", state: "REVIEWED_WITH_SOURCES", due: true, closed: true }, { title: "Future", state: "NOT_DUE" }, { title: "Changed", state: "QUESTION_CHANGED" }, { title: "Due later", state: "DUE", due_at: "2026-10-02" }, { title: "Due earlier", state: "DUE", due_at: "2026-10-01" }];
  assert.deepEqual(orderedFollowups(source).map(r => r.title), ["Due earlier", "Due later", "Changed", "Future", "Done"]);
  assert.equal(source[0].title, "Done");
});
test("finding comparison shows only changed human-readable fields including removed links", () => {
  const result = findingChoiceChanges({ status: "ACCEPTED", followup: "QUESTION", question_id: "question_old", note: "same" }, { status: "ACCEPTED", followup: "DEFER", note: "same" });
  assert.deepEqual(result, [{ field: "Follow-up choice", saved: "Confirmed question", draft: "Deferred" }, { field: "Linked question", saved: "question_old", draft: "Not recorded" }]);
});
