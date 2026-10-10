import { expect, test } from "@playwright/test";

test("capability inventory distinguishes public entries and treats missing usage as unknown", async ({ page }) => {
  const writes: string[] = [];
  await page.route("**/api/console/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    if (request.method() !== "GET") writes.push(path);
    if (path === "/api/capabilities") return route.fulfill({ json: { count: 24, items: [] } });
    if (path === "/api/capability-usage") return route.fulfill({ json: {
      rows: [], initialized: false, completeness: "best_effort",
      inventory: { public_tools: 9, capabilities: 24, operations: 106 },
    } });
    if (path === "/api/agent/status") return route.fulfill({ json: {
      enabled: false, configured: false, providers: [], models: [], components: {},
    } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/capabilities");
  await expect(page.getByRole("heading", { name: "Capability Usage", exact: true })).toBeVisible();
  await expect(page.getByText("9 public tools · 24 business capabilities · 106 operations", { exact: true })).toBeVisible();
  await expect(page.getByText("No recorded invocations are available yet.", { exact: true })).toBeVisible();
  await expect(page.getByText(/No observations does not mean unused/)).toBeVisible();
  expect(writes).toEqual([]);
});

test("observed usage combines retained daily rows without invoking capabilities", async ({ page }) => {
  const writes: string[] = [];
  await page.route("**/api/console/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    if (request.method() !== "GET") writes.push(path);
    if (path === "/api/capabilities") return route.fulfill({ json: { count: 24, items: [] } });
    if (path === "/api/capability-usage") return route.fulfill({ json: {
      rows: [3, 7].map((calls) => ({ source: "MCP", capability: "research_get", operation: "state", calls })),
      initialized: true, completeness: "best_effort",
      inventory: { public_tools: 9, capabilities: 24, operations: 106 },
    } });
    if (path === "/api/agent/status") return route.fulfill({ json: {
      enabled: false, configured: false, providers: [], models: [], components: {},
    } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/capabilities");
  await page.getByText("Most Observed Operations", { exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: "research_get" });
  await expect(row).toHaveCount(1);
  await expect(row.getByRole("cell").last()).toHaveText("10");
  expect(writes).toEqual([]);
});
