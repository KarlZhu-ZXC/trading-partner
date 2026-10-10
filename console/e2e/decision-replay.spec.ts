import { expect, test, type Page } from "@playwright/test";
import { RESEARCH_SAMPLE, DECISION_SAMPLE, syntheticResearchWorkspace, syntheticResearchLinks, syntheticDecisionReplay } from "../dev/research-workflow-fixtures";

async function setup(page: Page, wrongScope = false) {
  const reads: string[] = [];
  const writes: string[] = [];
  await page.route("**/api/console/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/console", "");
    if (route.request().method() !== "GET") { writes.push(path); return route.fulfill({ status: 403, json: {} }); }
    reads.push(path);
    if (path === "/api/research") return route.fulfill({ json: syntheticResearchWorkspace() });
    if (path.endsWith("/evidence-links")) return route.fulfill({ json: { data: syntheticResearchLinks() } });
    if (path.endsWith("/replay")) return route.fulfill({ json: { data: { ...syntheticDecisionReplay(), ...(wrongScope ? { subject_id: "case_other", title: "Wrong scope must stay hidden" } : {}) } } });
    return route.fulfill({ json: {} });
  });
  await page.goto(`/research?subject_id=${RESEARCH_SAMPLE}&section=evidence`);
  return { reads, writes };
}

test("Decision replay reads only the selected scope and discloses historical gaps", async ({ page }) => {
  const calls = await setup(page);
  const replay = page.locator("#research-decision-replay");
  await expect(replay.getByRole("heading", { name: "Decision Replay", exact: true })).toBeVisible();
  expect(calls.reads.some((path) => path.endsWith("/replay"))).toBe(false);
  await replay.getByRole("combobox", { name: "Reviewed Decision" }).selectOption(DECISION_SAMPLE);
  await replay.getByRole("button", { name: "Load Replay", exact: true }).click();
  await expect(replay.getByText("Historical state unavailable", { exact: true })).toBeVisible();
  await expect(replay.getByText(/Historical coverage is incomplete/)).toBeVisible();
  await expect(replay.getByRole("region", { name: "Historical Assumptions" })).toContainText("Contracted demand supports");
  expect(calls.reads.filter((path) => path.endsWith("/replay"))).toEqual([`/api/research/${RESEARCH_SAMPLE}/decisions/${DECISION_SAMPLE}/replay`]);
  expect(calls.writes).toEqual([]);
});

test("A mismatched replay response never displays another Subject's historical data", async ({ page }) => {
  await setup(page, true);
  const replay = page.locator("#research-decision-replay");
  await replay.getByRole("combobox", { name: "Reviewed Decision" }).selectOption(DECISION_SAMPLE);
  await replay.getByRole("button", { name: "Load Replay", exact: true }).click();
  await expect(replay.getByText(/did not match the selected Decision/)).toBeVisible();
  await expect(page.getByText("Wrong scope must stay hidden")).toHaveCount(0);
});

test("Replay remains readable at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  const replay = page.locator("#research-decision-replay");
  await replay.getByRole("combobox", { name: "Reviewed Decision" }).selectOption(DECISION_SAMPLE);
  await replay.getByRole("button", { name: "Load Replay", exact: true }).click();
  await expect(replay.getByText("Historical state unavailable", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
