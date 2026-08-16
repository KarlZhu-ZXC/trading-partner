import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

const APP_ROOT = path.resolve("app");
const SMALL_WORDS = new Set([
  "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
  "nor", "of", "on", "or", "per", "the", "to", "via", "vs", "with",
]);
const BRAND_WORDS = new Set(["iCloud", "launchd", "macOS", "moomoo", "yfinance"]);
const TEXT_ELEMENTS = new Set(["ActionButton", "button", "dt", "h1", "h2", "h3", "summary", "th"]);

function sourceFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(target) : target.endsWith(".tsx") ? [target] : [];
  });
}

function violatesTitleCase(input) {
  const text = input
    .replace(/&apos;/g, "'")
    .replace(/&(?:amp|quot);/g, " ")
    .replace(/\b[A-Za-z]+_[A-Za-z_]+\b/g, " ")
    .replace(/\([a-z]\)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!text || text.length > 100 || /[.!?…]$/.test(text) || /[{}]/.test(text)) return false;
  if (/^A-share\b/.test(text)) return false;

  const words = text.match(/[A-Za-z][A-Za-z’'/-]*/g) ?? [];
  if (words.length < 2 || words.length > 12) return false;
  for (let wordIndex = 0; wordIndex < words.length; wordIndex += 1) {
    for (const rawPart of words[wordIndex].split(/[-/]/)) {
      const word = rawPart.replace(/^[^A-Za-z]+|[^A-Za-z]+$/g, "");
      if (!word || word === "v" || BRAND_WORDS.has(word) || /^[A-Z0-9]+$/.test(word) || /[a-z][A-Z]/.test(word)) continue;
      const lower = word.toLowerCase();
      if (wordIndex > 0 && wordIndex < words.length - 1 && SMALL_WORDS.has(lower)) {
        if (word !== lower) return true;
      } else if (word[0] !== word[0].toUpperCase()) {
        return true;
      }
    }
  }
  return false;
}

function staticTextFindings(file) {
  const source = fs.readFileSync(file, "utf8");
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const findings = [];
  const record = (node, text) => {
    if (!violatesTitleCase(text)) return;
    findings.push(`${path.relative(APP_ROOT, file)}:${tree.getLineAndCharacterOfPosition(node.getStart()).line + 1} — ${text}`);
  };

  function visit(node) {
    if (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer)) {
      const attribute = node.name.text;
      const owner = node.parent.parent.tagName.getText(tree);
      if (attribute === "label" || attribute === "aria-label" || (attribute === "title" && owner === "Card")) {
        record(node, node.initializer.text);
      }
    }
    if (ts.isPropertyAssignment(node) && ts.isIdentifier(node.name) && node.name.text === "label" && ts.isStringLiteral(node.initializer)) {
      record(node, node.initializer.text);
    }
    if (ts.isJsxElement(node)) {
      const tag = node.openingElement.tagName.getText(tree);
      let inspectText = TEXT_ELEMENTS.has(tag);
      if (tag === "span") {
        const parent = node.parent;
        inspectText = ts.isJsxElement(parent) && parent.openingElement.tagName.getText(tree) === "label";
      }
      if (inspectText) {
        for (const child of node.children) {
          if (ts.isJsxText(child)) record(child, child.text.replace(/\s+/g, " ").trim());
          if (ts.isJsxExpression(child) && child.expression) {
            const inspectExpression = (expression) => {
              if (ts.isStringLiteral(expression)) record(expression, expression.text);
              ts.forEachChild(expression, inspectExpression);
            };
            inspectExpression(child.expression);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return findings;
}

test("Console control, field, tab, card, and table labels use Title Case", () => {
  const findings = sourceFiles(APP_ROOT).flatMap(staticTextFindings);
  assert.deepEqual(findings, [], `Sentence-case UI labels found:\n${findings.join("\n")}`);
});

function requiredFieldFindings(file) {
  const source = fs.readFileSync(file, "utf8");
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const findings = [];

  function tagName(node) {
    if (ts.isJsxSelfClosingElement(node)) return node.tagName.getText(tree);
    if (ts.isJsxElement(node)) return node.openingElement.tagName.getText(tree);
    return "";
  }

  function attributes(node) {
    if (ts.isJsxSelfClosingElement(node)) return node.attributes.properties;
    if (ts.isJsxElement(node)) return node.openingElement.attributes.properties;
    return [];
  }

  function hasAttribute(node, name) {
    return attributes(node).some((attribute) => ts.isJsxAttribute(attribute) && attribute.name.text === name);
  }

  function hasVisibleRequiredLabel(node) {
    for (let parent = node.parent; parent; parent = parent.parent) {
      if (!ts.isJsxElement(parent)) continue;
      const tag = tagName(parent);
      const markup = parent.getText(tree);
      if (tag === "Field" && hasAttribute(parent, "required")) return true;
      if (tag === "label" && (markup.includes("required-mark") || /<FieldLabel\s+required(?:=|\s|>)/.test(markup))) return true;
    }
    const prefix = source.slice(Math.max(0, node.getStart() - 300), node.getStart());
    return prefix.includes("required-mark");
  }

  function visit(node) {
    if (ts.isJsxSelfClosingElement(node) && ["input", "select", "textarea"].includes(tagName(node)) && hasAttribute(node, "required")) {
      const compactPlanCondition = path.basename(file) === "research-continuity.tsx" && hasAttribute(node, "aria-label");
      if (!hasVisibleRequiredLabel(node) && !compactPlanCondition) {
        const line = tree.getLineAndCharacterOfPosition(node.getStart()).line + 1;
        findings.push(`${path.relative(APP_ROOT, file)}:${line} — required control has no visible red-asterisk label`);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return findings;
}

test("Every native required Console control has a visible red-asterisk label", () => {
  const findings = sourceFiles(APP_ROOT).flatMap(requiredFieldFindings);
  assert.deepEqual(findings, [], findings.join("\n"));
  const continuity = fs.readFileSync(path.join(APP_ROOT, "research/research-continuity.tsx"), "utf8");
  assert.match(continuity, /required-mark[^\n]*Plan Conditions/, "Compact Trade Plan condition fields need a visible required group label");
});
