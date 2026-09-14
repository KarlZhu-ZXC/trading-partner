import { expect, test } from "@playwright/test";
import { syntheticTechnicalChartScene } from "../dev/chart-fixtures";

test("interactive technical chart loads indicators, SMC, styles, and drawing controls", async ({ page }) => {
  await page.route("**/api/console/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api\/console/, "").replace(/^\/api/, "");
    if (path === "/session") return route.fulfill({ json: { token: "synthetic-chart-token-00000000000000000000" } });
    if (path === "/capabilities") return route.fulfill({ json: { count: 0, items: [] } });
    if (path === "/agent/status") return route.fulfill({ json: { enabled: false, configured: false, providers: [], models: [], components: {} } });
    if (path === "/tools/invoke" && request.method() === "POST") {
      const payload = request.postDataJSON() as { tool_name?: string };
      expect(payload.tool_name).toBe("technical_get_snapshot");
      const data = syntheticTechnicalChartScene();
      return route.fulfill({
        json: {
          tool_name: payload.tool_name,
          result: {
            ok: true,
            warnings: [],
            errors: [],
            data,
          },
        },
      });
    }
    return route.fulfill({ json: { items: [] } });
  });

  await page.goto("/capabilities");
  await page.getByRole("button", { name: "Close Agent Panel", exact: true }).click();
  await page.getByRole("button", { name: "Open Chart", exact: true }).click();

  const chart = page.getByRole("img", { name: /interactive candlestick chart/i });
  await expect(chart).toBeVisible();
  await expect(chart.locator("canvas").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "EMA", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "SMC", exact: true })).toHaveAttribute("aria-pressed", "true");

  await page.getByLabel("Chart Style").selectOption("ohlc");
  await page.getByRole("button", { name: "MA", exact: true }).click();
  await expect(page.getByRole("button", { name: "MA", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Drawing Tool").selectOption("horizontalStraightLine");
  await page.getByRole("button", { name: "Draw", exact: true }).click();
  const box = await chart.boundingBox();
  expect(box).not.toBeNull();
  await chart.click({ position: { x: box!.width * 0.55, y: box!.height * 0.35 } });
  await expect(page.getByText("1 drawing", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Clear Drawings", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Clear Drawings", exact: true }).click();
  await expect(page.getByText("0 drawings", { exact: true })).toBeVisible();
});
