import { expect, test } from "@playwright/test";

const envelope = (data: Record<string, unknown>) => ({ ok: true, data, errors: [], warnings: [], degraded: false });
const routes = ["/", "/portfolio", "/research", "/monitors", "/agenda", "/scorecards", "/operations", "/capabilities", "/lan-login"];

for (const theme of ["light", "dark"]) test(`all Console surfaces fit supported widths in ${theme} theme`, async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.addInitScript((value) => { localStorage.setItem("trading-partner-theme", value); }, theme);
  const writes: string[] = [];
  await page.route("**/api/console/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname.replace(/^\/api\/console/, "").replace(/^\/api/, "");
    if (request.method() !== "GET") { writes.push(path); return route.fulfill({ status: 403, json: { detail: "Synthetic layout test rejects writes" } }); }
    if (path === "/session") return route.fulfill({ json: { token: "synthetic-layout-token-00000000000000000000" } });
    if (path === "/account-aliases") return route.fulfill({ json: { aliases: { sample_ira: "Schwab IRA" } } });
    if (path.startsWith("/agent")) return route.fulfill({ json: { enabled: false, configured: false, providers: [], models: [], components: {}, items: [] } });
    if (path === "/portfolio") return route.fulfill({ json: { accounts: envelope({ accounts: [{ snapshot_id: "sample_snapshot", account_ref: "sample_ira", provider: "schwab", environment: "REAL", base_currency: "USD", net_assets: "12000", cash: "1000", account_as_of: "2026-09-01T20:00:00Z", fetched_at: "2026-09-01T20:01:00Z", positions: [{ instrument_id: "equity:US:AAPL", quantity: "10", market_value: "1500", average_cost: "100", currency: "USD", side: "LONG" }], open_orders: [], warning_codes: [] }] }), transactions: envelope({ transactions: [] }), trade_cycles: envelope({ cycles: [], status: "COMPLETE" }) } });
    return route.fulfill({ json: { items: [], subjects: [], count: 0, partial_failures: [] } });
  });
  for (const width of [390, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const route of routes) {
      const response = await page.goto(route);
      expect(response?.status()).toBe(200);
      await expect(page.locator("h1").first()).toBeVisible();
      await expect(page.getByText("Loading local facts…", { exact: true })).toHaveCount(0);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      if (route !== "/lan-login") await expect(page.getByRole("button", { name: `${theme === "light" ? "Light" : "Dark"} Theme`, exact: true })).toHaveAttribute("aria-pressed", "true");
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      if (width > 1100 && route !== "/lan-login") {
        const panel = page.locator("#console-agent-panel");
        await expect.poll(() => panel.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
        const handle = page.getByRole("separator", { name: "Resize Copilot Panel" });
        await expect(handle).toBeVisible();
        expect((await handle.boundingBox())!.width).toBeLessThanOrEqual(16);
      }
      if (width === 1440 && route === "/portfolio") {
        await page.getByRole("button", { name: "Close navigation panel", exact: true }).click();
        await expect(page.locator(".nav-label").first()).toBeHidden();
        await expect.poll(() => page.locator(".sidebar").evaluate((node) => node.getBoundingClientRect().width)).toBeLessThanOrEqual(80);
        await page.getByRole("button", { name: "Open navigation panel", exact: true }).click();
        await expect(page.locator(".nav-label").first()).toBeVisible();
        const resize = page.getByRole("separator", { name: "Resize Copilot Panel" });
        const originalWidth = Number(await resize.getAttribute("aria-valuenow"));
        await resize.press("ArrowLeft");
        await expect.poll(async () => Number(await resize.getAttribute("aria-valuenow"))).toBeGreaterThan(originalWidth);
        await resize.press("ArrowRight");
        await expect.poll(async () => Number(await resize.getAttribute("aria-valuenow"))).toBe(originalWidth);
      }
      if (width === 1440 && ["/portfolio", "/monitors", "/operations"].includes(route)) {
        await page.screenshot({ path: testInfo.outputPath(`${theme}-${width}-${route.slice(1)}.png`) });
      }
    }
  }
  expect(writes).toEqual([]);
});
