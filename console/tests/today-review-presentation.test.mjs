import assert from "node:assert/strict";
import test from "node:test";
import { filterReviewGroups, normalizeReviewPriority, sourceLabel } from "../app/lib/today-review-presentation.ts";

const reason = (id, source_type, title, detail) => ({
  reason_id: id,
  source_type,
  source_id: `source_${id}`,
  subject_id: "subject_one",
  title,
  detail,
  occurred_at: "2026-09-15T08:00:00Z",
  due_at: null,
  href: `/open/${id}`,
  severity: "ATTENTION",
});

const group = (id, title, priority, source, reasonTitle, reasonDetail, subjectTitle = "Alpha Research") => ({
  group_id: id,
  instrument_id: `equity:US:${id}`,
  title,
  status: "ACTIVE",
  priority,
  subjects: [{ subject_id: `subject_${id}`, title: subjectTitle, href: `/research?subject_id=subject_${id}&opaque=keep` }],
  reasons: [reason(`${id}_reason`, source, reasonTitle, reasonDetail)],
  review_due_at: null,
});

test("sourceLabel covers daily review source families, including research questions", () => {
  assert.equal(sourceLabel("OBSERVATION_REVIEW_DUE"), "Notes");
  assert.equal(sourceLabel("MONITOR_ALERT"), "Monitoring");
  assert.equal(sourceLabel("OPEN_QUESTION_DUE"), "Research");
  assert.equal(sourceLabel("BROKER_ACTION"), "Broker");
});

test("filterReviewGroups matches group, instrument, subject, reason, detail, and human source text", () => {
  const groups = [
    group("AAPL", "Demand review", "ATTENTION", "OBSERVATION_REVIEW_DUE", "New note", "Check margin pressure"),
    group("MSFT", "Earnings follow-up", "DUE", "MONITOR_ALERT", "Threshold crossed", "Check monitoring output", "Cloud Research"),
  ];
  assert.deepEqual(filterReviewGroups(groups, "demand", "ALL"), [groups[0]]);
  assert.deepEqual(filterReviewGroups(groups, "equity:US:MSFT", "ALL"), [groups[1]]);
  assert.deepEqual(filterReviewGroups(groups, "cloud research", "ALL"), [groups[1]]);
  assert.deepEqual(filterReviewGroups(groups, "margin pressure", "ALL"), [groups[0]]);
  assert.deepEqual(filterReviewGroups(groups, "monitoring", "ALL"), [groups[1]]);
  assert.deepEqual(filterReviewGroups(groups, "  DEMAND  ", "ALL"), [groups[0]]);
});

test("priority selection is validated and filters only complete groups", () => {
  const groups = [
    group("AAPL", "Demand review", "ATTENTION", "OBSERVATION_REVIEW_DUE", "New note", "Check margin pressure"),
    group("MSFT", "Earnings follow-up", "DUE", "MONITOR_ALERT", "Threshold crossed", "Check monitoring output"),
    group("TSLA", "Error follow-up", "ERROR", "BROKER_ACTION", "Broker issue", "Reconnect broker"),
  ];
  assert.equal(normalizeReviewPriority("DUE"), "DUE");
  assert.equal(normalizeReviewPriority("invalid"), "ALL");
  assert.deepEqual(filterReviewGroups(groups, "", "DUE"), [groups[1]]);
  assert.deepEqual(filterReviewGroups(groups, "", "invalid"), groups);
});

test("filtering does not mutate groups or narrow subjects, reasons, or exact links", () => {
  const multi = group("AAPL", "Demand review", "ATTENTION", "OBSERVATION_REVIEW_DUE", "New note", "Check margin pressure");
  multi.subjects.push({ subject_id: "subject_two", title: "Second Research", href: "/research?subject_id=subject_two&keep=opaque" });
  multi.reasons.push(reason("second_reason", "BROKER_ACTION", "Broker action", "Keep exact item link"));
  const original = structuredClone(multi);
  const filtered = filterReviewGroups([multi], "second research", "ALL");
  assert.equal(filtered.length, 1);
  assert.strictEqual(filtered[0], multi);
  assert.deepEqual(filtered[0], original);
  assert.equal(filtered[0].subjects[1].href, "/research?subject_id=subject_two&keep=opaque");
  assert.equal(filtered[0].reasons[1].href, "/open/second_reason");
});
