/**
 * Review Queue due-date conversion.
 *
 * Shared by the home Review Queue and the Decision Workbench so both render
 * the same validation instead of throwing RangeError from toISOString()
 * before their NaN guard can run.  Matches the historical page semantics:
 * the timestamp is the local end of the requested day.
 */

const STRICT_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Convert a YYYY-MM-DD string to the end-of-day ISO timestamp used by
 * review-item transitions.  Returns null for empty input, malformed strings,
 * or calendar-invalid dates (such as 2026-02-30) that Date would otherwise
 * silently roll over to the next valid day; the caller decides how to
 * surface the validation error.
 */
export function endOfDayIsoOrNull(dateText) {
  const trimmed = String(dateText ?? "").trim();
  if (!trimmed) return null;
  if (!STRICT_DATE.test(trimmed)) return null;
  const [year, month, day] = trimmed.split("-").map(Number);
  const parsed = new Date(year, month - 1, day, 23, 59, 59, 0);
  if (
    parsed.getFullYear() !== year
    || parsed.getMonth() !== month - 1
    || parsed.getDate() !== day
  ) {
    return null;
  }
  return parsed.toISOString();
}
