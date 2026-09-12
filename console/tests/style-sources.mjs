import { readFile, readdir } from "node:fs/promises";

/** Read module-owned styles, normalizing only scoping syntax for legacy
 * structural assertions. Visual ownership is checked separately by AST/CSS tests. */
export async function consoleStyles() {
  const folders = ["styles", "components/ui"];
  const files = ["globals.css", "tokens.css", "decision-workbench/journal.module.css"];
  for (const folder of folders) {
    for (const name of await readdir(new URL(`../app/${folder}/`, import.meta.url))) {
      if (name.endsWith(".module.css")) files.push(`${folder}/${name}`);
    }
  }
  const css = (await Promise.all(files.map((file) => readFile(new URL(`../app/${file}`, import.meta.url), "utf8")))).join("\n");
  return css.replace(/\.root(?=:global\()/g, "").replace(/\.root\s+(?=:global\()/g, "").replace(/:global\(([^)]+)\)/g, "$1");
}
