/**
 * Presentation-only parsing for the host-owned field text emitted by
 * `copilot_research_evidence`.
 *
 * This module deliberately does not verify a field against a receipt. The
 * backend has already produced the durable answer text; the Console only
 * makes the value and its attached context easier to scan. Any text that is
 * not an exact, bounded field representation is left for the normal Agent
 * message renderer.
 */

const FIELD_PATH = /^result(?:\/[A-Za-z0-9_.:-]+)+$/;
const FIELD_HEAD = /^(result(?:\/[A-Za-z0-9_.:-]+)+): ([^\n〔〕]+?)(?: · (.*))?$/;
const FIELD_HINT = /\bresult(?:\/[A-Za-z0-9_.:-]+)+\s*:/;
const INLINE_FIELD = /〔[^\n〕]*〕/g;
const CODE_SPAN = /`+[^`\n]*`+/g;
const FENCE = /^\s*(?:```|~~~)/;
const ANSWER_METADATA_KEYS = new Set(["as_of", "basis", "evidence"]);
const ANSWER_METADATA_LINE = /^`([^`\r\n]+)`$/;
const SAFE_EVIDENCE_REF = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$/;
const MAX_FIELD_TEXT = 4_000;
const MAX_FIELD_VALUE = 160;

/** Metadata keys written by the backend catalog. */
export const RESEARCH_EVIDENCE_CONTEXT_KEYS = new Set([
  "instrument_id",
  "account_ref",
  "metric_code",
  "unit",
  "units",
  "scale",
  "period_end",
  "period_type",
  "adjustment",
  "currency",
  "as_of",
  "observed_at",
  "timestamp",
  "published_at",
  "quote_at",
  "snapshot_at",
  "source_as_of",
  "price_basis",
  "basis",
  "freshness",
  "source",
  "sources",
  "warnings",
  "degraded",
  "confirmed",
  "confirmed_at",
  "status",
  "direction",
  "url",
  "source_url",
]);

const FIELD_LABELS: Record<string, string> = {
  last: "Last Price",
  display_price: "Display Price",
  bid: "Bid",
  ask: "Ask",
  open: "Open",
  high: "High",
  low: "Low",
  close: "Close",
  previous_close: "Previous Close",
  volume: "Volume",
  change: "Change",
  change_pct: "Change %",
  quantity: "Quantity",
  price: "Price",
  market_value: "Market Value",
  cost_basis: "Cost Basis",
  average_cost: "Average Cost",
  current_average_cost: "Current Average Cost",
  unrealized_pnl: "Unrealized P&L",
  realized_pnl: "Realized P&L",
  net_trading_pnl: "Net Trading P&L",
  fees: "Fees",
  dividend_income: "Dividend Income",
  total_pnl: "Total P&L",
  revenue: "Revenue",
  net_income: "Net Income",
  operating_income: "Operating Income",
  free_cash_flow: "Free Cash Flow",
  total_assets: "Total Assets",
  total_liabilities: "Total Liabilities",
  equity: "Equity",
  eps: "EPS",
  pe_ratio: "P/E Ratio",
  rsi: "RSI",
  atr: "ATR",
  ema: "EMA",
  sma: "SMA",
  value: "Value",
  status: "Status",
  confirmed: "Confirmed",
  confirmed_at: "Confirmed Time",
  confirmation_time: "Confirmation Time",
  occurred_at: "Occurred Time",
  direction: "Direction",
  level: "Level",
  upper: "Upper",
  lower: "Lower",
  strength: "Strength",
  freshness: "Freshness",
  price_basis: "Price Basis",
  currency: "Currency",
  instrument_id: "Instrument ID",
  as_of: "As Of",
  observed_at: "Observed Time",
  published_at: "Published Time",
  timestamp: "Timestamp",
  quote_at: "Quote Time",
  snapshot_at: "Snapshot Time",
  source_as_of: "Source As Of",
  period_end: "Period End",
  period_type: "Period Type",
  unit: "Unit",
  units: "Units",
  scale: "Scale",
  adjustment: "Adjustment",
  basis: "Basis",
  source: "Source",
  sources: "Sources",
  warnings: "Warnings",
  degraded: "Degraded",
  account_ref: "Account",
  metric_code: "Metric Code",
  url: "URL",
  source_url: "Source URL",
};

export type ResearchEvidenceField = {
  raw: string;
  path: string;
  fieldKey: string;
  label: string;
  scalar: string;
  isNull: boolean;
  metadata: Readonly<Record<string, string>>;
};

export type ResearchAnswerMetadata = {
  raw: string;
  metadata: Readonly<Record<string, string>>;
};

export type ResearchEvidenceSegment =
  | { kind: "text"; text: string }
  | { kind: "field"; field: ResearchEvidenceField; inline: boolean }
  | { kind: "metadata"; metadata: ResearchAnswerMetadata };

function fieldKeyFromPath(path: string): string {
  const parts = path.split("/");
  const last = parts[parts.length - 1];
  if (last !== undefined && !/^\d+$/.test(last)) return last;
  if (parts[parts.length - 2] === "source_urls") return "source_url";
  return "field";
}

function validScalar(value: string): boolean {
  return value.length > 0
    && value.length <= MAX_FIELD_VALUE
    && value === value.trim()
    && !value.includes("·")
    && !value.includes("〔")
    && !value.includes("〕")
    && !/[\r\n]/.test(value);
}

function parseMetadata(value: string | undefined): Readonly<Record<string, string>> | null {
  if (!value) return null;
  const parts = value.split(" · ");
  if (parts.length === 0 || parts.some((part) => part.length === 0)) return null;
  const metadata: Record<string, string> = {};
  for (const part of parts) {
    const separator = part.indexOf("=");
    if (separator <= 0) return null;
    const key = part.slice(0, separator);
    const item = part.slice(separator + 1);
    if (!RESEARCH_EVIDENCE_CONTEXT_KEYS.has(key)
      || Object.prototype.hasOwnProperty.call(metadata, key)
      || !/^[A-Za-z][A-Za-z0-9_]*$/.test(key)
      || !validScalar(item)) {
      return null;
    }
    metadata[key] = item;
  }
  return metadata;
}

/**
 * Parse one exact catalog entry such as
 * `result/quotes/0/last: 100 · currency=USD · quote_at=...`.
 */
export function parseResearchEvidenceField(value: string): ResearchEvidenceField | null {
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_FIELD_TEXT) return null;
  const match = value.match(FIELD_HEAD);
  if (!match) return null;
  const path = match[1];
  const scalar = match[2];
  if (!FIELD_PATH.test(path) || !validScalar(scalar)) return null;
  const metadata = parseMetadata(match[3]);
  // Every catalog entry includes at least one context item (the receipt's
  // degraded flag), so a bare path/value is intentionally not canonical.
  if (metadata === null) return null;
  const fieldKey = fieldKeyFromPath(path);
  const label = FIELD_LABELS[fieldKey];
  if (!label) return null;
  return {
    raw: value,
    path,
    fieldKey,
    label,
    scalar,
    isNull: scalar === "null",
    metadata,
  };
}

/**
 * Parse the standalone answer metadata line appended by `render_agent_answer`.
 * It is kept separate from field parsing so an evidence reference can never
 * be presented as a sourced value.
 */
export function parseResearchAnswerMetadata(value: string): ResearchAnswerMetadata | null {
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_FIELD_TEXT) return null;
  const match = value.match(ANSWER_METADATA_LINE);
  if (!match) return null;
  const parts = match[1].split(" · ");
  if (parts.length === 0 || parts.some((part) => part.length === 0)) return null;
  const metadata: Record<string, string> = {};
  for (const part of parts) {
    const separator = part.indexOf("=");
    if (separator <= 0) return null;
    const key = part.slice(0, separator);
    const item = part.slice(separator + 1);
    if (!ANSWER_METADATA_KEYS.has(key)
      || Object.prototype.hasOwnProperty.call(metadata, key)
      || (key !== "evidence" && !validScalar(item))) {
      return null;
    }
    if (key === "evidence") {
      const refs = item.split(", ");
      if (refs.length > 20 || new Set(refs).size !== refs.length || refs.some((ref) => !SAFE_EVIDENCE_REF.test(ref))) return null;
    }
    metadata[key] = item;
  }
  return { raw: value, metadata };
}

function lineRanges(value: string): Array<{ start: number; end: number; text: string; code: boolean }> {
  const ranges: Array<{ start: number; end: number; text: string; code: boolean }> = [];
  const linePattern = /[^\r\n]*(?:\r\n|\n|\r|$)/g;
  let inFence = false;
  for (const match of value.matchAll(linePattern)) {
    const text = match[0].replace(/(?:\r\n|\n|\r)$/, "");
    const start = match.index ?? 0;
    const end = start + text.length;
    const code = inFence;
    ranges.push({ start, end, text, code });
    if (FENCE.test(text)) inFence = !inFence;
    if (match[0].length === 0) break;
  }
  return ranges;
}

function isInsideCodeSpan(value: string, offset: number): boolean {
  for (const match of value.matchAll(CODE_SPAN)) {
    const start = match.index ?? 0;
    if (offset > start && offset < start + match[0].length) return true;
  }
  return false;
}

function unmatchedInlineMarker(value: string): boolean {
  const withoutFields = value.replace(INLINE_FIELD, "");
  return withoutFields.includes("〔") || withoutFields.includes("〕");
}

function looksLikePlainField(value: string): boolean {
  return FIELD_HINT.test(value);
}

function looksLikeAnswerMetadata(value: string): boolean {
  return /^`(?:as_of|basis|evidence)=/.test(value);
}

/**
 * Split an answer into normal Markdown text and exact host-rendered field
 * spans. A null result means the caller must use the lossless normal renderer.
 */
export function segmentResearchEvidence(value: string): ResearchEvidenceSegment[] | null {
  if (typeof value !== "string" || value.length === 0) return null;
  if (unmatchedInlineMarker(value)) return null;

  const ranges = lineRanges(value);
  const candidates: Array<{
    start: number;
    end: number;
    field?: ResearchEvidenceField;
    metadata?: ResearchAnswerMetadata;
    inline?: boolean;
  }> = [];

  for (const match of value.matchAll(INLINE_FIELD)) {
    const start = match.index ?? 0;
    const token = match[0];
    const inner = token.slice(1, -1);
    const field = parseResearchEvidenceField(inner);
    const line = ranges.find((item) => start >= item.start && start <= item.end);
    if (!field || line?.code || isInsideCodeSpan(value, start)) return null;
    candidates.push({ start, end: start + token.length, field, inline: true });
  }

  for (const line of ranges) {
    if (line.code || !line.text || candidates.some((item) => item.start < line.end && item.end > line.start)) continue;
    const answerMetadata = parseResearchAnswerMetadata(line.text);
    if (answerMetadata) {
      candidates.push({ start: line.start, end: line.end, metadata: answerMetadata });
      continue;
    }
    if (looksLikeAnswerMetadata(line.text)) return null;
    const field = parseResearchEvidenceField(line.text);
    if (field) {
      candidates.push({ start: line.start, end: line.end, field, inline: false });
    } else if (looksLikePlainField(line.text)) {
      // A path-looking line that is not canonical must remain in the normal
      // renderer; this also prevents a malformed marker from being partly
      // prettified around an accidental number.
      return null;
    }
  }

  if (candidates.length === 0) return null;
  candidates.sort((left, right) => left.start - right.start);
  let cursor = 0;
  const segments: ResearchEvidenceSegment[] = [];
  for (const candidate of candidates) {
    if (candidate.start < cursor) return null;
    if (candidate.start > cursor) segments.push({ kind: "text", text: value.slice(cursor, candidate.start) });
    if (candidate.field) {
      segments.push({ kind: "field", field: candidate.field, inline: candidate.inline ?? false });
    } else if (candidate.metadata) {
      segments.push({ kind: "metadata", metadata: candidate.metadata });
    } else {
      return null;
    }
    cursor = candidate.end;
  }
  if (cursor < value.length) segments.push({ kind: "text", text: value.slice(cursor) });
  return segments;
}

export function humanizeResearchEvidenceField(field: ResearchEvidenceField): string {
  return field.label;
}
