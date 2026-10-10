import { expect, test } from "@playwright/test";

test("Overview reads are passive and queue updates require an explicit click", async ({ page }) => {
  const writes: { path: string; body: unknown }[] = [];
  let overviewReads = 0;
  let refreshed = false;
  const scanReceipt = { status: "PARTIAL", complete: false, cutoff_at: "2026-10-02T08:00:00Z",
    receipt_window: { started_at: "2026-10-02T08:00:00Z", completed_at: "2026-10-02T08:00:01Z" },
    sources: { DECISION_REVIEW_DUE: { status: "COMPLETE", row_count: 102, pages: 2 },
      OBSERVATION_REVIEW_DUE: { status: "PARTIAL", row_count: 100, pages: 1 } },
    persistence: { status: "SAVED", receipt_id: "audit_synthetic_refresh" } };
  await page.route("**/api/console/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    if (request.method() !== "GET") {
      writes.push({ path, body: request.postDataJSON() });
      refreshed = true;
      return route.fulfill({ json: { updated_count: 1, complete: false, scan_receipt: scanReceipt } });
    }
    if (path === "/api/session") {
      return route.fulfill({ json: { token: "synthetic-console-session-token-for-tests" } });
    }
    if (path === "/api/overview") {
      overviewReads += 1;
      return route.fulfill({ json: {
        review_coverage: { complete: false, materialized_at: null, materialization_required: true },
        recent_refresh_receipt: refreshed ? scanReceipt : null,
        recent_refresh_receipt_status: refreshed ? "AVAILABLE" : "NONE",
      } });
    }
    if (path === "/api/agent/status") {
      return route.fulfill({ json: { enabled: false, configured: false, providers: [], models: [] } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  await page.getByText("Background Workspace Details", { exact: true }).click();
  const update = page.getByRole("button", { name: "Update Review Queue", exact: true });
  await expect(update).toBeVisible();
  expect(writes).toEqual([]);
  await page.reload();
  await page.getByText("Background Workspace Details", { exact: true }).click();
  await expect(update).toBeVisible();
  expect(writes).toEqual([]);
  const priorReads = overviewReads;
  await update.click();
  await expect(page.getByRole("status").filter({ hasText: "Some sources remain incomplete" })).toBeVisible();
  expect(writes).toEqual([{ path: "/api/review-items/refresh", body: { confirmation: "refresh_review_items" } }]);
  await expect.poll(() => overviewReads).toBeGreaterThan(priorReads);
  await page.getByText("Last Queue Refresh", { exact: true }).click();
  await expect(page.getByText(/Sources were checked independently/)).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "DECISION REVIEW DUE" })).toContainText("102");
  await page.reload();
  await page.getByText("Background Workspace Details", { exact: true }).click();
  await page.getByText("Last Queue Refresh", { exact: true }).click();
  await expect(page.getByRole("row").filter({ hasText: "DECISION REVIEW DUE" })).toContainText("102");
  expect(writes).toHaveLength(1);
});
