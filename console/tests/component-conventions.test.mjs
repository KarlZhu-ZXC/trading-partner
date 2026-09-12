import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import postcss from "postcss";

const app = path.resolve("app");
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => e.isDirectory() ? walk(path.join(dir,e.name)) : [path.join(dir,e.name)]);
const nativeControls = new Set(["button", "input", "select", "textarea", "table", "a"]);
export function rawControlCounts(file) {
  const source = fs.readFileSync(file, "utf8"); const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX); const found = {};
  function visit(node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(tree);
      if (nativeControls.has(tag)) found[tag] = (found[tag] ?? 0) + 1;
    }
    ts.forEachChild(node, visit);
  }
  visit(tree); return found;
}

test("every Console surface uses the shared control layer", () => {
  const errors = [];
  for (const file of walk(app).filter((p) => p.endsWith(".tsx"))) {
    const relative = path.relative(app, file);
    if (relative === "components/ui.tsx" || relative.startsWith("components/ui/")) continue;
    for (const [tag,count] of Object.entries(rawControlCounts(file))) {
      if (count > 0) errors.push(`${relative}: ${count} raw <${tag}>; use a shared component`);
    }
  }
  assert.deepEqual(errors, []);
});

test("surface styles do not redefine shared controls or use literal color values", () => {
  const cssFiles = ["app/decision-workbench/journal.module.css", ...walk("app/styles").filter((p) => p.endsWith(".module.css"))];
  const forbidden = new Set(["color", "background", "background-color", "border", "border-color", "border-radius", "font", "font-size", "font-family", "box-shadow"]);
  const errors = [];
  for (const file of cssFiles) postcss.parse(fs.readFileSync(file, "utf8")).walkDecls((d) => {
    if (/#[a-f0-9]{3,8}\b|rgba?\(/i.test(d.value)) errors.push(`${d.prop}: literal color`);
    if (/\b(button|input|select|textarea)\b|\.(action-button|quick-link|badge|card-head)\b/.test(d.parent.selector ?? "") && forbidden.has(d.prop)) errors.push(`${d.parent.selector}: ${d.prop}`);
  });
  assert.deepEqual(errors, []);
});

test("all CSS custom properties resolve or are explicitly runtime-owned", () => {
  const css = walk(app).filter((p) => p.endsWith(".css")).map((p) => fs.readFileSync(p,"utf8")).join("\n");
  const defined = new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const dynamic = new Set(["--agent-rail-user-width", "--entity-per-page"]);
  const used = new Set([...css.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]));
  assert.deepEqual([...used].filter((v) => !defined.has(v) && !dynamic.has(v)), []);
});
