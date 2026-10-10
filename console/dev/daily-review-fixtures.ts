// Synthetic UI examples only; never load owner notes or accounts here.
import { RESEARCH_SAMPLE, SYNTHETIC_SUBJECTS } from "./research-workflow-fixtures.ts";

export function syntheticDailyReview() {
  const at = "2026-09-22T08:00:00Z";
  const groups = [
    { symbol: "AAPL", title: "Apple", priority: "DUE", status: "ACTIVE", reason: "Review the earnings assumptions", source: "DECISION_REVIEW_DUE" },
    { symbol: "MSFT", title: "Microsoft", priority: "ATTENTION", status: "ACTIVE", reason: "New cloud investment note", source: "OBSERVATION_REVIEW_DUE" },
    { symbol: "NVDA", title: "NVIDIA", priority: "ERROR", status: "ACTIVE", reason: "Latest monitoring data is incomplete", source: "MONITOR_ERROR" },
    { symbol: "GOOG", title: "Alphabet", priority: "ATTENTION", status: "DEFERRED", reason: "Wait for the next business update", source: "DECISION_REVIEW_DUE" },
  ].map((item) => {
    const definition = SYNTHETIC_SUBJECTS.find((subject) => subject.instrument_id === `equity:US:${item.symbol}`);
    // Every linked sample is explicit. Apple is the canonical W01 journey;
    // other rows remain useful populated examples with their own Subject IDs.
    const subjectId = definition?.subject_id ?? (item.symbol === "AAPL" ? RESEARCH_SAMPLE : `sample_subject_${item.symbol}`);
    const title = definition?.title ?? `${item.title} Research`;
    const href = `/research?subject_id=${encodeURIComponent(subjectId)}&section=quick-review`;
    return {
      group_id: `sample_group_${item.symbol}`, instrument_id: `equity:US:${item.symbol}`,
      title: item.title, status: item.status, priority: item.priority,
      subjects: [{ subject_id: subjectId, title, href }],
      reasons: [{ reason_id: `sample_reason_${item.symbol}`, source_type: item.source, source_id: `sample_source_${item.symbol}`, subject_id: subjectId, title: item.reason, detail: "Compare the saved evidence with the current reviewed assumptions.", occurred_at: at, due_at: item.priority === "DUE" ? at : null, href, severity: item.priority }],
      review_due_at: item.priority === "DUE" ? at : null,
    };
  });
  return { as_of: at, timezone: "Asia/Shanghai", groups, unscoped: [{ reason_id: "sample_connection", source_type: "BROKER_ACTION", source_id: "sample_connection", subject_id: null, title: "Check the broker connection", detail: "An operational follow-up needs separate attention.", occurred_at: at, due_at: null, href: "/operations", severity: "ERROR" }], coverage: { research: "COMPLETE", notes: "COMPLETE", monitoring: "COMPLETE" }, warnings: [] };
}
