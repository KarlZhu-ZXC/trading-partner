import { expect, test, type Page } from "@playwright/test";
const href = "/research?subject_id=case_synthetic&section=quick-review&revision_id=rev_exact";
const data = { as_of: "2026-09-15T01:00:00Z", week_start: "2026-09-14T00:00:00+08:00", week_end: "2026-09-21T00:00:00+08:00", timezone: "Asia/Shanghai",
  confirmed_views: [{ subject_id: "case_synthetic", subject_title: "Synthetic Subject", thesis_id: "thesis_synthetic", revision_id: "rev_exact", revision_no: 2, confirmed_at: "2026-09-15T00:00:00Z", kind: "CHANGED", before_statement: "Synthetic prior statement", after_statement: "Synthetic confirmed statement", changed_fields: ["statement"], href }],
  decisions: [{ subject_id: "case_synthetic", subject_title: "Synthetic Subject", decision_id: "decision_wait", decision_type: "no_action", title: "Wait for evidence", rationale: "Synthetic review remains open", recorded_at: "2026-09-15T00:00:00Z", review_due_at: null, href: "/research?subject_id=case_synthetic&section=quick-review&decision_id=decision_wait" }],
  open_questions: [{ subject_id: "case_synthetic", question_id: "question_synthetic", text: "What evidence changes this view?", asked_at: "2026-09-01T00:00:00Z", status: "OPEN", href: "/research?subject_id=case_synthetic&section=quick-review&question_id=question_synthetic" }],
  due_groups: [], unresolved_groups: [], coverage: { CONFIRMED_VIEWS: "COMPLETE", TODAY: "COMPLETE" }, warnings: [] as string[] };
async function setup(page: Page, partial = false) {
  const writes: string[] = []; const reads: string[] = [];
  await page.route("**/api/console/api/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/console", "");
    if (route.request().method() !== "GET") { writes.push(path); return route.fulfill({ status: 403, json: { detail: "Unexpected write" } }); }
    reads.push(path);
    if (path === "/api/weekly-review") return route.fulfill({ json: { data: partial ? { ...data, confirmed_views: [], coverage: { CONFIRMED_VIEWS: "UNAVAILABLE", TODAY: "COMPLETE" }, warnings: ["CONFIRMED_VIEWS_SOURCE_UNAVAILABLE"] } : data } });
    return route.fulfill({ json: {} });
  });
  return { reads, writes };
}
async function openWeekly(page: Page) {
  await page.goto("/");
  const tab = page.getByRole("tab", { name: "Weekly Review", exact: true });
  await expect(tab).toBeVisible();
  await tab.click();
  return page.locator("#weekly-review");
}
test("weekly review exposes exact differences and scoped links with only durable GETs", async ({ page }) => {
  const api = await setup(page); const card = await openWeekly(page);
  await expect(card.getByRole("heading", { name: "Weekly Review", exact: true })).toBeVisible();
  await expect(card.getByText(/Asia\/Shanghai/)).toBeVisible();
  await expect(card.getByText("Synthetic confirmed statement", { exact: true })).toBeVisible();
  await expect(card.getByRole("link", { name: "Review This Subject" })).toHaveAttribute("href", href);
  await card.getByText("Exact Revision Difference", { exact: true }).click();
  await expect(card.getByText("Before: Synthetic prior statement", { exact: true })).toBeVisible();
  await expect(card.getByText("After: Synthetic confirmed statement", { exact: true })).toBeVisible();
  await expect(card.getByText("What evidence changes this view?", { exact: true })).toBeVisible();
  await expect(card.getByText("Wait for evidence", { exact: false })).not.toBeVisible();
  await card.getByText("Recorded Decisions · 1", { exact: true }).click();
  await expect(card.getByText("no_action", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "Refresh Weekly Review" }).click();
  await expect.poll(() => api.reads.filter((path) => path === "/api/weekly-review").length).toBe(2);
  expect(api.writes).toEqual([]);
});
test("missing weekly source is explicit instead of an all-clear result", async ({ page }) => {
  await setup(page, true); const card = await openWeekly(page);
  await expect(card.getByText(/Some sources are incomplete or unavailable/)).toBeVisible();
  await expect(card.getByText(/No confirmed Thesis revisions/)).toHaveCount(0);
});
