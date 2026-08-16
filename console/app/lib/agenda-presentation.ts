import { asRecord } from "./coerce";

export type AgendaSummary = {
  upcoming7d: number;
  upcoming: number;
  overdue: number;
  coverageGap: number;
};

function count(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

/** Format the canonical backend Agenda summary without reclassifying items. */
export function agendaSummaryFromPayload(payload: unknown): AgendaSummary {
  const root = asRecord(payload);
  const agenda = asRecord(root.agenda);
  const envelope = Object.keys(agenda).length > 0 ? agenda : root;
  const data = Object.keys(asRecord(envelope.data)).length > 0
    ? asRecord(envelope.data)
    : envelope;
  const summary = asRecord(data.summary);
  return {
    upcoming7d: count(summary.upcoming_7d_count),
    upcoming: count(summary.upcoming_count),
    overdue: count(summary.overdue_count),
    coverageGap: count(summary.coverage_gap_count),
  };
}
