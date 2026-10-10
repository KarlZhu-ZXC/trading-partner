import { expect, test, type Page } from "@playwright/test";

import { syntheticDailyReview } from "../dev/daily-review-fixtures";
import {
  DECISION_SAMPLE,
  RESEARCH_SAMPLE,
  syntheticQuickReview,
  syntheticResearchChanges,
  syntheticResearchLinks,
  syntheticResearchWorkspace,
} from "../dev/research-workflow-fixtures";

async function mockWorkflowPreview(page: Page) {
  const reads: string[] = [];
  const writes: string[] = [];
  await page.route("**/api/console/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    if (request.method() !== "GET") {
      writes.push(path);
      return route.fulfill({ status: 403, json: { detail: "Synthetic preview writes are disabled" } });
    }
    reads.push(path);
    if (path === "/api/review-digest") return route.fulfill({ json: { data: syntheticDailyReview() } });
    if (path === "/api/research") return route.fulfill({ json: syntheticResearchWorkspace() });
    if (path === `/api/research/${RESEARCH_SAMPLE}/quick-review`) return route.fulfill({ json: { data: syntheticQuickReview() } });
    if (path.startsWith(`/api/research/${RESEARCH_SAMPLE}/changes`)) return route.fulfill({ json: { data: syntheticResearchChanges() } });
    if (path === `/api/research/${RESEARCH_SAMPLE}/evidence-links`) return route.fulfill({ json: { data: syntheticResearchLinks() } });
    if (path === `/api/research/${RESEARCH_SAMPLE}/decisions/${DECISION_SAMPLE}/replay`) return route.fulfill({ json: { data: { subject_id: RESEARCH_SAMPLE, decision_id: DECISION_SAMPLE } } });
    if (path === "/api/session") return route.fulfill({ json: { token: "synthetic-workflow-preview-session-token-000000" } });
    if (path === "/api/agent/status") return route.fulfill({ json: { enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} } });
    if (path.startsWith("/api/monitors")) return route.fulfill({ json: { dashboard: { ok: true, data: { items: [] } } } });
    if (path.startsWith("/api/observations")) return route.fulfill({ json: { data: { external_notes: [], observation_sources: [] } } });
    return route.fulfill({ json: {} });
  });
  return { reads, writes };
}

test("Today Review opens the same Subject in Quick Review and Evidence", async ({ page }) => {
  const api = await mockWorkflowPreview(page);
  await page.goto("/");
  const today = page.locator("#today-review");
  const apple = today.locator("article").filter({ hasText: "Apple" }).first();
  await expect(apple).toContainText("Review the earnings assumptions");
  await apple.getByRole("link", { name: "Quick Review", exact: true }).click();

  await expect(page).toHaveURL(new RegExp(`subject_id=${RESEARCH_SAMPLE}.*section=quick-review`));
  await expect(page.getByRole("tabpanel", { name: "Quick Review", exact: true }).getByText("Maintain the reviewed long-term judgment", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Latest Moomoo thinking/ })).toBeVisible();
  await expect(page.getByText("2 notes still need their own review.", { exact: false })).toBeVisible();

  await page.getByRole("tab", { name: "Evidence", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evidence Comparison", exact: true })).toBeVisible();
  await expect(page.getByRole("strong").filter({ hasText: "Quarterly filing" })).toBeVisible();
  expect(api.reads).toContain(`/api/research/${RESEARCH_SAMPLE}/quick-review`);
  expect(api.reads).toContain(`/api/research/${RESEARCH_SAMPLE}/evidence-links`);
  expect(api.reads.some((path) => path.startsWith(`/api/research/${RESEARCH_SAMPLE}/changes`))).toBe(true);
  expect(api.writes).toEqual([]);
});


test("preview HTTP routes preserve exact identity, error states and write protection", async ({ request }) => {
  test.skip(process.env.CONSOLE_DESIGN_PREVIEW !== "1", "Enable the isolated synthetic preview API for this contract test.");
  const base = `/api/design-preview/api/research/${RESEARCH_SAMPLE}`;
  const quick = await request.get(`${base}/quick-review`);
  expect(quick.status()).toBe(200);
  expect((await quick.json()).data.subject_id).toBe(RESEARCH_SAMPLE);
  const missing = await request.get("/api/design-preview/api/research/case_unknown/quick-review");
  expect(missing.status()).toBe(404);
  const otherDecision = await request.get(`${base}/decisions/decision_other/replay`);
  expect(otherDecision.status()).toBe(404);
  const empty = await request.get(`${base}/quick-review?preview_state=empty`);
  expect((await empty.json()).data.can_maintain).toBe(false);
  const failed = await request.get(`${base}/changes?preview_state=error`);
  expect(failed.status()).toBe(500);
  const write = await request.post(`${base}/quick-review`, { data: { confirmed: true } });
  expect(write.status()).toBe(403);
});
