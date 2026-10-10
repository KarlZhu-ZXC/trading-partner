import assert from "node:assert/strict";
import test from "node:test";
import {
  parseResearchEvidenceField,
  parseResearchAnswerMetadata,
  parseResearchPresentation,
  segmentResearchEvidence,
} from "../app/lib/research-evidence-presentation.ts";

const lastPrice = (instrument, currency, quoteAt, scalar = "100") =>
  `result/quotes/0/last: ${scalar} · instrument_id=${instrument} · currency=${currency} · quote_at=${quoteAt} · sources=SYNTHETIC · degraded=false`;

function typedField({
  ref = "req_typed",
  path = "result/quotes/0/last",
  scalar = "100",
  is_null = scalar === "null",
  context = [["instrument_id", "equity:US:AAPL"], ["currency", "USD"], ["quote_at", "2026-09-15T12:00:00Z"]],
  inline = true,
} = {}) {
  return { kind: "field", ref: `${ref}/${path}`, path, scalar, is_null, context, inline };
}

function renderedTypedField(field) {
  const raw = `${field.path}: ${field.scalar}${field.context.length ? ` · ${field.context.map(([key, value]) => `${key}=${value}`).join(" · ")}` : ""}`;
  return field.inline ? `〔${raw}〕` : raw;
}

test("parses canonical field text and humanizes common labels without changing the value", () => {
  const raw = lastPrice("equity:US:AAPL", "USD", "2026-09-15T12:00:00Z");
  const field = parseResearchEvidenceField(raw);
  assert.ok(field);
  assert.equal(field.path, "result/quotes/0/last");
  assert.equal(field.fieldKey, "last");
  assert.equal(field.label, "Last Price");
  assert.equal(field.scalar, "100");
  assert.equal(field.metadata.instrument_id, "equity:US:AAPL");
  assert.equal(field.metadata.currency, "USD");
  assert.equal(field.metadata.quote_at, "2026-09-15T12:00:00Z");
  assert.equal(field.metadata.degraded, "false");
});

test("keeps subjects, currencies, and times on separate inline fields", () => {
  const first = lastPrice("equity:US:AAPL", "USD", "2026-09-15T12:00:00Z", "100");
  const second = lastPrice("equity:JP:7203", "JPY", "2026-09-14T03:00:00Z", "200");
  const segments = segmentResearchEvidence(
    `## 推断\n\n比较〔${first}〕与〔${second}〕，两者不能合并判断。`,
  );
  assert.ok(segments);
  const fields = segments.filter((segment) => segment.kind === "field");
  assert.equal(fields.length, 2);
  assert.equal(fields[0].field.metadata.instrument_id, "equity:US:AAPL");
  assert.equal(fields[0].field.metadata.currency, "USD");
  assert.equal(fields[0].field.metadata.quote_at, "2026-09-15T12:00:00Z");
  assert.equal(fields[1].field.metadata.instrument_id, "equity:JP:7203");
  assert.equal(fields[1].field.metadata.currency, "JPY");
  assert.equal(fields[1].field.metadata.quote_at, "2026-09-14T03:00:00Z");
  assert.equal(segments[0].text, "## 推断\n\n比较");
  assert.equal(segments.at(-1).text, "，两者不能合并判断。");
});

test("preserves null distinctly from zero", () => {
  const nullField = parseResearchEvidenceField(
    "result/portfolio/fees: null · currency=USD · degraded=false",
  );
  const zeroField = parseResearchEvidenceField(
    "result/portfolio/fees: 0 · currency=USD · degraded=false",
  );
  assert.ok(nullField);
  assert.ok(zeroField);
  assert.equal(nullField.scalar, "null");
  assert.equal(nullField.isNull, true);
  assert.equal(zeroField.scalar, "0");
  assert.equal(zeroField.isNull, false);
});

test("malformed, unknown, and bare marker-like text falls back to the normal renderer", () => {
  const valid = lastPrice("equity:US:AAPL", "USD", "2026-09-15T12:00:00Z");
  assert.equal(parseResearchEvidenceField(valid.replace("currency=USD", "unknown=USD")), null);
  assert.equal(parseResearchEvidenceField(valid.replace(" · degraded=false", " ·")), null);
  assert.equal(parseResearchEvidenceField("result/quotes/0/last: 100"), null);
  assert.equal(segmentResearchEvidence(`说明〔${valid.replace("currency=USD", "unknown=USD")}〕。`), null);
  assert.equal(segmentResearchEvidence("普通数字 100 和日期 2026-09-15。"), null);
  assert.equal(segmentResearchEvidence("result/quotes/0/last: 100"), null);
});

test("does not reinterpret fields in code and keeps an unclosed marker lossless", () => {
  const valid = lastPrice("equity:US:AAPL", "USD", "2026-09-15T12:00:00Z");
  assert.equal(segmentResearchEvidence(`\`〔${valid}〕\``), null);
  assert.equal(segmentResearchEvidence(`\`\`\`\n${valid}\n\`\`\``), null);
  assert.equal(segmentResearchEvidence(`说明〔${valid}。`), null);
});

test("long grouped references fold without losing the raw metadata line", () => {
  const refs = Array.from({ length: 8 }, (_, index) => `req_synthetic/result/quotes/${index}/market_value`);
  const raw = "`evidence=" + refs.join(", ") + "`";
  const metadata = parseResearchAnswerMetadata(raw);
  assert.ok(metadata);
  assert.equal(metadata.raw, raw);
  assert.equal(segmentResearchEvidence("## 推断\n\n需要对齐币种。\n\n" + raw).at(-1).kind, "metadata");
  assert.equal(parseResearchAnswerMetadata("`evidence=req_good, req_good`"), null);
  assert.equal(parseResearchAnswerMetadata("`unknown=req_good`"), null);
});

test("unknown fields and duplicate metadata remain unformatted", () => {
  assert.equal(parseResearchEvidenceField("result/mystery: 1 · degraded=false"), null);
  assert.equal(parseResearchEvidenceField("result/fees: 0 · currency=USD · currency=CNY"), null);
});

test("typed host presentation reconstructs the exact message and projects fields without reparsing text", () => {
  const field = typedField({ context: [["instrument_id", "equity:US:AAPL"], ["currency", "USD"], ["freshness", "literal · equals=x"]] });
  const metadata = { kind: "metadata", text: "`as_of=2026-09-15T12:00:00Z · basis=literal · equals=x · evidence=req_typed/result/quotes/0/last`" };
  const content = `## 推断\n\n价格为${renderedTypedField(field)}，保留原解释。\n\n${metadata.text}`;
  const parsed = parseResearchPresentation({ schema_version: 1, segments: [
    { kind: "text", text: "## 推断\n\n价格为" },
    field,
    { kind: "text", text: "，保留原解释。\n\n" },
    metadata,
  ] }, content);
  assert.ok(parsed);
  assert.equal(parsed[0].kind, "text");
  assert.equal(parsed[1].kind, "field");
  assert.equal(parsed[1].field.metadata.freshness, "literal · equals=x");
  assert.equal(parsed[2].kind, "text");
  assert.equal(parsed[3].kind, "metadata");
  assert.equal(parsed[3].metadata.raw, metadata.text);
  assert.equal(segmentResearchEvidence(content), null);
});

test("typed presentation rejects tampering, mismatches, unknown keys, and true null flag mismatches", () => {
  const field = typedField();
  const content = renderedTypedField(field);
  const presentation = { schema_version: 1, segments: [field] };
  assert.ok(parseResearchPresentation(presentation, content));
  assert.equal(parseResearchPresentation(presentation, `${content} `), null);
  assert.equal(parseResearchPresentation({ ...presentation, extra: true }, content), null);
  assert.equal(parseResearchPresentation({ schema_version: 1, segments: [{ ...field, ref: "req_typed/result/quotes/0/close" }] }, content), null);
  assert.equal(parseResearchPresentation({ schema_version: 1, segments: [{ ...field, context: [["unknown", "x"]] }] }, content), null);
  assert.equal(parseResearchPresentation({ schema_version: 1, segments: [{ ...field, is_null: true }] }, content), null);
  const literalNull = typedField({ scalar: "null", is_null: false });
  assert.ok(parseResearchPresentation({ schema_version: 1, segments: [literalNull] }, renderedTypedField(literalNull)));
  assert.equal(parseResearchPresentation({ schema_version: 1, segments: [{ ...literalNull, text: "unexpected" }] }, renderedTypedField(literalNull)), null);
});

test("typed null and zero fields remain distinct while preserving delimiter data", () => {
  const nullField = typedField({ path: "result/portfolio/fees", scalar: "null", is_null: true, context: [["currency", "USD · reported=net"], ["basis", "zero=not-null"]], inline: false });
  const zeroField = typedField({ path: "result/portfolio/fees", scalar: "0", is_null: false, context: [["currency", "USD · reported=net"], ["basis", "zero=not-null"]], inline: false });
  const nullContent = renderedTypedField(nullField);
  const zeroContent = renderedTypedField(zeroField);
  const parse = (field, content) => parseResearchPresentation({ schema_version: 1, segments: [field] }, content);
  const parsedNull = parse(nullField, nullContent);
  const parsedZero = parse(zeroField, zeroContent);
  assert.ok(parsedNull);
  assert.ok(parsedZero);
  assert.equal(parsedNull[0].field.scalar, "null");
  assert.equal(parsedNull[0].field.isNull, true);
  assert.equal(parsedNull[0].field.metadata.basis, "zero=not-null");
  assert.equal(parsedZero[0].field.scalar, "0");
  assert.equal(parsedZero[0].field.isNull, false);
});
