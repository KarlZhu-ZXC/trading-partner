/**
 * Durable Catalyst Agenda sync-receipt unwrapping.
 *
 * Extracted from the Agenda page so the envelope tolerance of the receipt
 * parser can be unit-tested without rendering React.  Keep this module
 * dependency-free and synchronous.
 */

function asDict(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};
}

function text(value, fallback = "") {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : fallback;
}

function asInt(value, fallback) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.trunc(parsed);
}

function pickText(source, data, result, key) {
  return text(source[key], text(data[key], text(result[key])));
}

function pickInt(source, data, result, key) {
  return asInt(source[key] ?? data[key] ?? result[key], 0);
}

function pickList(source, data, result, key) {
  const candidate = source[key] ?? data[key] ?? result[key];
  return Array.isArray(candidate) ? candidate : [];
}

function providerResults(source, data, result) {
  const rows = pickList(source, data, result, "provider_results");
  const normalized = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    normalized.push({
      vendor: text(row.vendor),
      scope_ref: text(row.scope_ref),
      status: text(row.status),
      candidate_count: asInt(row.candidate_count, 0),
      error_code: text(row.error_code) || null,
      warning_codes: Array.isArray(row.warning_codes) ? row.warning_codes : [],
    });
  }
  return normalized;
}

/**
 * Accept the sync receipt at any of the envelope shapes the Console API has
 * returned over time (bare, {data}, or {result}) and normalize it.  Returns
 * null when no receipt_id survives, which is how the page detects an
 * unparseable response.
 */
export function unwrapAgendaSync(payload) {
  const source = asDict(payload);
  const data = asDict(source.data);
  const result = asDict(source.result);

  const receipt = {
    receipt_id: pickText(source, data, result, "receipt_id"),
    status: pickText(source, data, result, "status"),
    as_of: pickText(source, data, result, "as_of"),
    window_start: pickText(source, data, result, "window_start"),
    window_end: pickText(source, data, result, "window_end"),
    scope_count: pickInt(source, data, result, "scope_count"),
    eligible_instrument_count: pickInt(source, data, result, "eligible_instrument_count"),
    succeeded_scope_count: pickInt(source, data, result, "succeeded_scope_count"),
    failed_scope_count: pickInt(source, data, result, "failed_scope_count"),
    candidate_count: pickInt(source, data, result, "candidate_count"),
    appended_count: pickInt(source, data, result, "appended_count"),
    revised_count: pickInt(source, data, result, "revised_count"),
    date_drift_count: pickInt(source, data, result, "date_drift_count"),
    unchanged_count: pickInt(source, data, result, "unchanged_count"),
    provider_results: providerResults(source, data, result),
    // The raw value is already the array; the historical page code passed it
    // through listOf(value, "a"), which silently dropped every code.
    limitation_codes: pickList(source, data, result, "limitation_codes"),
    started_at: pickText(source, data, result, "started_at"),
    completed_at: pickText(source, data, result, "completed_at"),
    schema_version: pickInt(source, data, result, "schema_version"),
    execution_effect: Boolean(
      source.execution_effect ?? data.execution_effect ?? result.execution_effect,
    ),
  };

  if (!receipt.receipt_id) return null;
  return receipt;
}
