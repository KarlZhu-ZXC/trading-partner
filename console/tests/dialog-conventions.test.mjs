import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const OWNED_CONSOLE_FILES = [
  "app/components/agent-rail.tsx",
  "app/components/ui.tsx",
  "app/decision-workbench/page.tsx",
  "app/monitors/page.tsx",
  "app/operations/page.tsx",
  "app/research/page.tsx",
  "app/research/research-continuity.tsx",
];

test("owned Console actions use semantic dialogs instead of native browser prompts", async () => {
  const sources = await Promise.all(OWNED_CONSOLE_FILES.map((file) => readFile(new URL(`../${file}`, import.meta.url), "utf8")));
  for (const source of sources) {
    assert.doesNotMatch(source, /\bwindow\.(?:alert|confirm|prompt)\s*\(/);
  }
  const ui = sources[1];
  assert.match(ui, /export function ConfirmationDialog/);
  assert.match(ui, /role="dialog"/);
  assert.match(ui, /aria-modal="true"/);
  assert.match(ui, /querySelectorAll<HTMLElement>/);
  assert.match(ui, /event\.key === "Tab"/);
  assert.match(ui, /previous\?\.focus\?\.\(\)/);
  assert.match(ui, /export function TextInputDialog/);
  assert.match(ui, /aria-required=\{required \|\| undefined\}/);
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(styles, /\.dialog-backdrop/);
  assert.match(styles, /\.dialog-panel/);
});
