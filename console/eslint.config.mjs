import eslint from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist/**", ".next/**"]),
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{js,mjs,ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      complexity: ["error", 160],
    },
  },
  {
    files: ["app/decision-workbench/page.tsx"],
    rules: {
      // Journal is the remaining consolidation hotspot. Ratchet its current
      // ceiling separately while extracted tabs and queries continue shrinking it.
      complexity: ["error", 240],
    },
  },
]);
