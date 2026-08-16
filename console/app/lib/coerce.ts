/**
 * Shared display coercion for durable Console payloads.
 *
 * Two text conventions coexist by design: dashboard pages render missing
 * values as an em-dash, while Agent surfaces never invent a placeholder for
 * absent data. Import the one the page already follows (`textDash as text` /
 * `textStrict as text`) so call sites keep their local naming.
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
