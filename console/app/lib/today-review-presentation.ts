/** Pure presentation helpers for the cross-source Today Review digest. */

export type Reason = {
  reason_id: string;
  source_type: string;
  source_id: string;
  subject_id: string | null;
  title: string;
  detail: string;
  occurred_at: string;
  due_at: string | null;
  href: string;
  severity: string;
};

export type ReviewGroup = {
  group_id: string;
  instrument_id: string | null;
  title: string;
  status: "ACTIVE" | "DEFERRED" | "REVIEWED";
  priority: "ERROR" | "DUE" | "ATTENTION";
  subjects: Array<{ subject_id: string; title: string; href: string }>;
  reasons: Reason[];
  review_due_at: string | null;
};

export type Digest = {
  as_of: string;
  timezone: string;
  groups: ReviewGroup[];
  unscoped: Reason[];
  coverage: Record<string, string>;
  warnings: string[];
};

export const REVIEW_PRIORITY_VALUES = ["ALL", "ERROR", "DUE", "ATTENTION"] as const;
export type ReviewPriority = (typeof REVIEW_PRIORITY_VALUES)[number];

/** Convert a source type into the label shown to a daily reviewer. */
export function sourceLabel(source: string): string {
  const normalized = String(source ?? "").toUpperCase();
  if (/OBSERVATION|NOTE/.test(normalized)) return "Notes";
  if (/MONITOR/.test(normalized)) return "Monitoring";
  if (/AGENDA|CATALYST/.test(normalized)) return "Catalysts";
  if (/THESIS|RESEARCH|QUESTION|DECISION|REVIEW/.test(normalized)) return "Research";
  if (/ORDER|BROKER/.test(normalized)) return "Broker";
  return "Operations";
}

export function normalizeReviewPriority(value: string): ReviewPriority {
  return (REVIEW_PRIORITY_VALUES as readonly string[]).includes(value)
    ? value as ReviewPriority
    : "ALL";
}

function searchableText(group: ReviewGroup): string {
  return [
    group.title,
    group.instrument_id ?? "",
    ...group.subjects.map((subject) => subject.title),
    ...group.reasons.flatMap((reason) => [
      reason.title,
      reason.detail,
      sourceLabel(reason.source_type),
    ]),
  ].join("\n").toLocaleLowerCase();
}

/**
 * Filter groups in memory while preserving each matching group's complete
 * subjects, reasons, and exact links. A search never narrows a group to a
 * guessed Research Subject or reason.
 */
export function filterReviewGroups(
  groups: readonly ReviewGroup[],
  query: string,
  priority: ReviewPriority | string,
): ReviewGroup[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const normalizedPriority = normalizeReviewPriority(priority);
  return groups.filter((group) => {
    if (normalizedPriority !== "ALL" && group.priority !== normalizedPriority) return false;
    return normalizedQuery.length === 0 || searchableText(group).includes(normalizedQuery);
  });
}
