import assert from "node:assert/strict";
import test from "node:test";
import { accountLabel } from "../app/lib/account-label.ts";

test("aliases follow exact accounts across balance changes and history without a provider", () => {
  const aliases = { schwab_small: "Schwab IRA", schwab_large: "Schwab Brokerage" };
  assert.equal(accountLabel({ account_ref: "schwab_small", net_assets: "9999999" }, aliases), "Schwab IRA");
  assert.equal(accountLabel({ account_ref: "schwab_large", net_assets: "1" }, aliases), "Schwab Brokerage");
  assert.equal(accountLabel({ account_ref: "schwab_small" }, aliases), "Schwab IRA");
});

test("unmapped accounts stay distinct without inheriting an alias", () => {
  assert.equal(accountLabel({ provider: "SCHWAB", account_ref: "schwab_abc123" }), "Schwab · ABC123");
  assert.equal(accountLabel({ provider: "SCHWAB", account_ref: "schwab_def456" }), "Schwab · DEF456");
  assert.equal(accountLabel({ provider: "MOOMOO", account_ref: "moomoo_abc123" }), "Moomoo · ABC123");
  assert.equal(accountLabel({ account_ref: "toString" }), "TOSTRING · STRING");
});
