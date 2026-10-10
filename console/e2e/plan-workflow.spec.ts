import { expect, test, type Page } from "@playwright/test";
import { RESEARCH_SAMPLE, syntheticResearchWorkspace, syntheticResearchLinks, syntheticResearchChanges } from "../dev/research-workflow-fixtures";

async function mock(page: Page, options: { unavailable?: boolean; changedPlan?: boolean } = {}) {
  const workspace = syntheticResearchWorkspace();
  const current = workspace.subjects[0].state.data.current_trade_plan;
  current.status = "ACTIVE";
  const writes: Array<Record<string, unknown>> = [];
  await page.route("**/api/console/**", async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/console", "");
    const json = (value: unknown, status = 200) => route.fulfill({ json: value, status });
    if (req.method() === "POST") {
      const body = req.postDataJSON(); writes.push(body);
      return json({ result: { ok: true, data: { overall_status: "INCOMPLETE", as_of: "2026-10-03T00:00:00Z", position_sizing: { plan_id: current.plan_id, plan_version: options.changedPlan ? 3 : 2, data_quality_codes: ["NAV_UNAVAILABLE", "CASH_UNAVAILABLE"], constraints: [{ constraint_code: "CASH", status: "NOT_EVALUATED", message: "Durable cash snapshot unavailable." }] } } } });
    }
    if (path === "/api/session") return json({ token: "synthetic-plan-workflow-console-session-token" });
    if (path === "/api/capabilities") return json({ count: 1, items: [{ name: "broker_order_manage", group: "Execution", description: "Synthetic gated order preview", operations: ["preview", "submit"], read_only: false, open_world: false, destructive: false, effect: "WRITE", confirmation_required: true, input_schema: {} }] });
    if (path === "/api/research") return json(workspace);
    if (path.endsWith("/evidence-links")) return json({ data: syntheticResearchLinks() });
    if (path.endsWith("/changes")) return json({ data: syntheticResearchChanges() });
    if (path.endsWith("/workflow")) return options.unavailable ? json({ detail: "Unavailable" }, 409) : json({ data: {
      plan_version: 2, as_of: "2026-10-03T00:00:00Z", previous_version: 1, previous_version_available: true,
      thesis: { revision_at_plan_creation: "rev_old", current_revision_id: "rev_new", review_needed: true },
      field_changes: [], condition_changes: [{ condition_code: "ENTRY", change: "CHANGED", before: { threshold: "99" }, after: { threshold: "100" } }],
      coverage: { unavailable: false, conditions: [{ condition_code: "ENTRY", state: "UNCONFIGURED", configured: false, observed: false }, { condition_code: "REVIEW", state: "MANUAL", configured: false, observed: false }], source_refs: [] },
      monitors_available: true, monitors: [{ monitor_id: "monitor_synthetic", version: 1, plan_version: 1, status: "ACTIVE", binding: "OTHER_VERSION" }],
    } });
    return json({ items: [] });
  });
  await page.goto(`/research?subject_id=${RESEARCH_SAMPLE}&section=trade-plan`);
  return writes;
}

test("new Plan retains old Monitor and requires explicit readiness action", async ({ page }) => {
  const writes = await mock(page);
  const panel = page.getByRole("region", { name: "Plan Dependency Review" });
  await expect(panel.getByText("UNCONFIGURED", { exact: true })).toBeVisible();
  await expect(panel.getByRole("link", { name: "Review Rules or Pause" })).toHaveAttribute("href", "/monitors?monitor_id=monitor_synthetic&version=1");
  await panel.getByRole("button", { name: "Keep Existing Rules" }).click();
  await expect(panel.getByRole("status")).toContainText("no Monitor was changed");
  await panel.getByRole("button", { name: "Defer Review" }).click();
  expect(writes).toEqual([]);
  await panel.getByRole("button", { name: "Check Sizing and Risk" }).click();
  await expect(panel.getByText("Durable cash snapshot unavailable.", { exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ tool_name: "portfolio_risk_get", arguments: { request: { refresh_accounts: false } } });
  await expect(panel.getByRole("link", { name: "Prepare Separate Order Preview" })).toHaveAttribute("href", /workflow=order-preview.*plan_version=2/);
  await expect(panel).toContainText("UNKNOWN submit outcome");
  await expect(panel.getByRole("link", { name: "Review Fills and Unattributed Activity" })).toHaveAttribute("href", /#cycles$/);
});

test("dependency failure preserves the confirmed Plan and does not invoke a write", async ({ page }) => {
  const writes = await mock(page, { unavailable: true });
  await expect(page.getByText("Dependencies unavailable; the confirmed Plan remains saved.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Propose New Version", exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});

test("readiness result for a newer Plan is refused", async ({ page }) => {
  await mock(page, { changedPlan: true });
  await page.getByRole("button", { name: "Check Sizing and Risk" }).click();
  await expect(page.getByText("Plan version changed. Reload Research before checking readiness again.")).toBeVisible();
  await expect(page.getByText("Durable cash snapshot unavailable.", { exact: true })).not.toBeVisible();
});


test("order preview handoff preserves exact Plan but never runs or confirms a tool", async ({ page }) => {
  const writes = await mock(page);
  await page.getByRole("link", { name: "Prepare Separate Order Preview" }).click();
  const draft = JSON.parse(await page.getByLabel("Arguments JSON").inputValue());
  expect(draft.request).toMatchObject({ operation: "preview", case_id: RESEARCH_SAMPLE, trade_plan_version: 2 });
  expect(writes).toEqual([]);
  await expect(page.getByRole("button", { name: "Run Tool" })).toBeVisible();
});


test("narrow Plan readiness remains keyboard-operable without automatic writes", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const writes = await mock(page);
  const button = page.getByRole("button", { name: "Check Sizing and Risk" });
  await button.focus();
  await expect(button).toBeFocused();
  expect(writes).toEqual([]);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("cell", { name: "Durable cash snapshot unavailable.", exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
