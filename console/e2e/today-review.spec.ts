import { expect, test, type Page } from "@playwright/test";
const AT = "2026-09-15T08:00:00Z";
function reason(id: string) { return { reason_id: id, source_type: "OBSERVATION_REVIEW_DUE", source_id: `note_${id}`, subject_id: "case_one", title: "Updated note", detail: "A new view needs review.", occurred_at: AT, due_at: null, href: "/decision-workbench#notes", severity: "ATTENTION" }; }
function group(id: string, title: string, status = "ACTIVE") { return { group_id: id, instrument_id: `equity:US:${id}`, title, status, priority: "ATTENTION", subjects: [{ subject_id: "case_one", title: "First Research", href: "/research?subject_id=case_one&section=quick-review" }], reasons: [reason(id)], review_due_at: status === "DEFERRED" ? "2026-09-18T08:00:00Z" : null }; }
async function mock(page: Page, mode: "normal" | "partial" | "failed" = "normal") {
  let failOverview = false;
  const writes: string[] = []; const reads: string[] = [];
  const first = group("ONE", "Single Scope Instrument");
  const multi = { ...group("TWO", "Multiple Scope Instrument"), subjects: [first.subjects[0], { subject_id: "case_two", title: "Second Research", href: "/research?subject_id=case_two&section=quick-review&keep=opaque" }] };
  await page.route("**/api/console/api/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname.replace("/api/console", "");
    if (request.method() !== "GET") { writes.push(path); return route.fulfill({ status: 403, json: { detail: "Unexpected write" } }); }
    reads.push(path);
    if (path === "/api/overview" && failOverview) return route.fulfill({ status: 500, json: { detail: "Synthetic overview unavailable" } });
    if (path === "/api/agent/status") return route.fulfill({ json: { enabled: false, configured: false, providers: [], models: [], components: {} } });
    if (path === "/api/review-digest") {
      if (mode === "failed") return route.fulfill({ status: 500, json: { detail: "Synthetic review source unavailable" } });
      return route.fulfill({ json: { data: { as_of: AT, timezone: "Asia/Shanghai", groups: mode === "partial" ? [] : [first, multi, group("LATER", "Deferred Instrument", "DEFERRED"), group("DONE", "Reviewed Instrument", "REVIEWED")], unscoped: mode === "partial" ? [] : [{ ...reason("broker"), subject_id: null, source_type: "BROKER_ACTION", title: "Broker connection needs attention", detail: "Open broker settings to recover the connection.", href: "/operations", severity: "ERROR" }], coverage: { research: "COMPLETE", notes: mode === "partial" ? "UNAVAILABLE" : "COMPLETE" }, warnings: mode === "partial" ? ["NOTES_UNAVAILABLE"] : [] } } });
    }
    return route.fulfill({ json: {} });
  });
  return { reads, writes, failDigest: () => { mode = "failed"; }, failOverview: () => { failOverview = true; } };
}
test("Today Review groups instruments, preserves scope links and hides deferred/reviewed rows", async ({ page }) => {
  const api = await mock(page); await page.goto("/?review_window=today");
  const card = page.locator("#today-review");
  await expect(card.getByRole("heading", { name: "Today Review", exact: true })).toBeVisible();
  const single = card.locator("article").filter({ has: page.getByText("Single Scope Instrument", { exact: true }) });
  await expect(single.getByRole("link", { name: "Quick Review", exact: true })).toHaveAttribute("href", "/research?subject_id=case_one&section=quick-review");
  const multi = card.locator("article").filter({ has: page.getByText("Multiple Scope Instrument", { exact: true }) });
  await expect(multi.getByRole("link", { name: "Quick Review", exact: true })).toHaveCount(0);
  await expect(multi.getByRole("link", { name: "Second Research", exact: true })).toHaveAttribute("href", "/research?subject_id=case_two&section=quick-review&keep=opaque");
  await expect(card.getByText("Deferred Instrument", { exact: true })).not.toBeVisible();
  await expect(card.getByText("Reviewed Instrument", { exact: true })).not.toBeVisible();
  await expect(card.getByText("Broker connection needs attention", { exact: false })).toBeVisible();
  await card.getByText("Deferred · 1", { exact: true }).click();
  await expect(card.getByText("Deferred Instrument", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "Refresh Today Review", exact: true }).click();
  await expect.poll(() => api.reads.filter((path) => path === "/api/review-digest").length).toBe(2);
  expect(api.writes).toEqual([]);
  await expect(page.getByRole("heading", { name: "View Inbox", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Action & Review Inbox", exact: true })).toHaveCount(0);
});
test("Partial digest never implies the review queue is clear", async ({ page }) => {
  await mock(page, "partial"); await page.goto("/?review_window=today");
  const card = page.locator("#today-review");
  await expect(card.getByText(/Some review sources are incomplete or unavailable/)).toBeVisible();
  await expect(card.getByText("No active review items in the available sources.")).toHaveCount(0);
});
test("Digest read failure offers refresh without an all-clear claim", async ({ page }) => {
  await mock(page, "failed"); await page.goto("/?review_window=today");
  const card = page.locator("#today-review");
  await expect(card.getByText("Synthetic review source unavailable")).toBeVisible();
  await expect(card.getByRole("button", { name: "Refresh Today Review" })).toBeEnabled();
  await expect(card.getByText("No active review items in the available sources.")).toHaveCount(0);
});

test("Daily filters retain exact scope links and keep operational actions visible", async ({ page }) => {
  const api = await mock(page); await page.goto("/?review_window=today");
  const card = page.locator("#today-review");
  await card.getByLabel("Search Reviews", { exact: true }).fill("Multiple Scope");
  await expect(card.getByText("Single Scope Instrument", { exact: true })).not.toBeVisible();
  await expect(card.getByRole("link", { name: "Second Research", exact: true })).toHaveAttribute("href", "/research?subject_id=case_two&section=quick-review&keep=opaque");
  await expect(card.getByRole("region", { name: "Other Actions" }).getByText(/Broker connection needs attention/)).toBeVisible();
  await card.getByRole("combobox", { name: "Priority", exact: true }).selectOption("DUE");
  await expect(card.getByText(/No active reviews match/)).toBeVisible();
  await expect(card.getByText("No active review items in the available sources.")).toHaveCount(0);
  await card.getByRole("button", { name: "Reset Filters", exact: true }).click();
  await expect(card.getByText("Single Scope Instrument", { exact: true })).toBeVisible();
  expect(api.reads.filter((path) => path === "/api/review-digest")).toHaveLength(1);
  expect(api.writes).toEqual([]);
});

test("Refresh failures preserve filtered reviews and overview with retry controls", async ({ page }) => {
  const api = await mock(page); await page.goto("/?review_window=today");
  const card = page.locator("#today-review");
  await card.getByLabel("Search Reviews", { exact: true }).fill("Multiple Scope");
  api.failDigest();
  await card.getByRole("button", { name: "Refresh Today Review", exact: true }).click();
  await expect(card.getByText("Synthetic review source unavailable")).toBeVisible();
  await expect(card.getByText("Multiple Scope Instrument", { exact: true })).toBeVisible();
  await expect(card.getByLabel("Search Reviews", { exact: true })).toHaveValue("Multiple Scope");
  api.failOverview();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByText("Synthetic overview unavailable", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Operations & Workflow", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry Overview", exact: true })).toBeEnabled();
  expect(api.writes).toEqual([]);
});

test("Weekly Review selection survives reload without losing unrelated URL context", async ({ page }) => {
  const api = await mock(page); await page.goto("/?agent=open#review-queue");
  await page.getByRole("tab", { name: "Weekly Review", exact: true }).click();
  await expect(page).toHaveURL(/review_window=week/);
  expect(new URL(page.url()).searchParams.get("agent")).toBe("open");
  expect(new URL(page.url()).hash).toBe("#review-queue");
  await page.reload();
  await expect(page.getByRole("tab", { name: "Weekly Review", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Changes and Risks", exact: true }).click();
  expect(new URL(page.url()).searchParams.get("review_window")).toBe("today");
  expect(api.writes).toEqual([]);
});
