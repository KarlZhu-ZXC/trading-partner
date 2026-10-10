type Row = Record<string, unknown>;
function priority(row: Row): number {
  if (row.closed === true || row.state === "REVIEWED_WITH_SOURCES") return 8;
  if (row.due === true || row.state === "DUE") return 0;
  if (["QUESTION_CHANGED", "REVIEW_CHANGED", "CLOSED_QUESTION_NEEDS_REVIEW"].includes(String(row.state))) return 1;
  if (["EVIDENCE_MISSING", "SOURCE_UNAVAILABLE"].includes(String(row.state))) return 2;
  if (row.state === "READY_FOR_REVIEW") return 3;
  if (row.state === "NO_DEADLINE") return 4;
  if (row.state === "NOT_DUE") return 5;
  if (row.state === "DEFERRED") return 6;
  if (row.state === "NO_FOLLOWUP_RECORDED") return 7;
  return 2;
}
function date(row: Row): number {
  const value = typeof row.due_at === "string" ? Date.parse(row.due_at) : NaN;
  return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
}
export function orderedFollowups<T extends Row>(rows: T[]): T[] {
  return [...rows].sort((a, b) => priority(a) - priority(b) || date(a) - date(b)
    || String(a.title ?? "").localeCompare(String(b.title ?? ""))
    || String(a.finding_key ?? "").localeCompare(String(b.finding_key ?? "")));
}
export function findingChoiceChanges(saved: Row, draft: Row): Array<{ field: string; saved: string; draft: string }> {
  const fields = [["status", "Finding disposition"], ["followup", "Follow-up choice"], ["note", "Reason"], ["question_id", "Linked question"], ["question_version", "Question version"]];
  const labels: Record<string, string> = { ACCEPTED: "Accepted", DISPUTED: "Disputed", RESOLVED: "Resolved", QUESTION: "Confirmed question", NO_FOLLOWUP: "No follow-up needed", DEFER: "Deferred" };
  return fields.flatMap(([key, field]) => {
    const before = saved[key] == null ? "" : String(saved[key]);
    const after = draft[key] == null ? "" : String(draft[key]);
    if (before === after) return [];
    return [{ field, saved: labels[before] ?? (before || "Not recorded"), draft: labels[after] ?? (after || "Not recorded") }];
  });
}
