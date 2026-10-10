/**
 * Shared display coercion for durable Console payloads.
 *
 * Text and numeric conventions differ by surface. Import the existing
 * convention explicitly; these display adapters are not validation or
 * evidence normalization and must not turn missing facts into known values.
 */

type Dict = Record<string, unknown>;

/** Dashboard convention: finite numbers render as text; trimmed non-empty
 * strings pass through; everything else shows the fallback. */
export function textDash(value: unknown, fallback = "—"): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : fallback;
}

/** Agent-surface convention: only non-empty strings pass; no invented dash. */
export function textStrict(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

/** Object guard that excludes arrays and null. */
export function asRecord(value: unknown): Dict {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Dict)
    : {};
}

/** Trim strings without accepting numbers as text (Journal and note identity). */
export function textTrimmed(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

/** Existing Cycle/Retro display: stringify present values, preserving whitespace. */
export function textStringified(value: unknown, fallback = "—"): string {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

/** Existing Journal arithmetic fallback; never use to certify a financial fact. */
export function numberOrZero(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** Legacy payload object access accepts arrays too; not a strict record validator. */
export function asObject(value: unknown): Dict {
  return value !== null && typeof value === "object" ? (value as Dict) : {};
}

/** Agent timestamp in the viewer timezone; preserve invalid source text. */
export function displayDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
