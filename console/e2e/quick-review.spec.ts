import { expect, test, type Page } from "@playwright/test";
const SUBJECT = "case_019ff000-0000-7000-8000-000000000081";
const INSTRUMENT = "equity:US:TEST";
async function mock(page: Page, failFirst = false, receipt: "NOT_FOUND" | "RECORDED" = "NOT_FOUND") {
  const posts: Record<string, unknown>[] = [];
  const reads: string[] = [];
  await page.route("**/api/console/api/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname.replace("/api/console", "");
    const json = (value: unknown) => route.fulfill({ json: value });
    if (request.method() === "POST") {
      posts.push(request.postDataJSON());
      if (!path.endsWith("/quick-review")) return route.fulfill({ status: 403, json: { detail: "Unexpected write" } });
      if (failFirst && posts.length === 1) return route.fulfill({ status: 500, json: { detail: "Synthetic unknown outcome; retry same intent" } });
      return json({ data: { decision_id: "decision_synthetic_recorded", action: posts.at(-1)!.action, status: "RECORDED", review_due_at: posts.at(-1)!.review_due_at } });
    }
    reads.push(path);
    if (path.includes("/quick-review/submissions/")) return json({ data: receipt === "RECORDED" ? { status: "RECORDED", decision_id: "decision_recovered", action: "maintain", review_due_at: null } : { status: "NOT_FOUND" } });
    if (path === "/api/session") return json({ token: "synthetic-quick-review-session-token-0000000000000" });
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, providers: [], models: [], components: {} });
    if (path === "/api/research") return json({ subjects: [{ subject: { subject_id: SUBJECT, title: "Synthetic Quick Subject", subject_type: "company", primary_instrument_id: INSTRUMENT, summary: "Synthetic research", status: "active", topic_tags: [], linked_subject_ids: [] }, state: { ok: true, data: { theses: [], latest_revisions: [], assumptions: [], invalidations: [], pending_candidates: [], trade_plan_versions: [], open_questions: [], watchlist_items: [] } } }].flatMap((item) => [{ ...item, subject: { ...item.subject, subject_id: "case_019ff000-0000-7000-8000-000000000082", title: "Other Subject Listed First" } }, item]) });
    if (path.endsWith("/quick-review")) return json({ data: { subject_id: SUBJECT, title: "Synthetic Quick Subject", instrument_id: INSTRUMENT, draft_baseline: null, review_token: "synthetic-pinned-token", expires_at: new Date(Date.now() + 600000).toISOString(), baseline: { decision_id: "decision_synthetic_baseline", title: "Prior reviewed judgment", decided_at: "2026-09-10T10:00:00Z", recorded_at: "2026-09-10T10:01:00Z", theses: [{ thesis_id: "thesis_synthetic", revision_id: "revision_synthetic", statement: "Prior synthetic statement" }], plan: { plan_id: "plan_synthetic", version: 2 } }, changes: [{ change_id: "change_synthetic", title: "Demand changed", kind: "OBSERVATION", old_value: "Stable demand", new_value: "Weaker demand", occurred_at: "2026-09-12T10:00:00Z" }], coverage: { observations: "COMPLETE", monitors: "UNAVAILABLE" }, warnings: ["MONITOR_READ_FAILED"], positions: [{ account_ref: "Synthetic Account", quantity: "10", currency: "USD", as_of: "2026-09-11T10:00:00Z", snapshot_id: "snapshot_synthetic" }], pending_observation_reviews: 2, can_maintain: true, latest_thinking: [{ note_id: "note_synthetic", note_revision_id: "note_revision_synthetic", version: 4, title: "Latest synthetic thinking", source_timestamp: "2026-09-12T10:00:00Z", observed_at: "2026-09-12T10:01:00Z", status: "EXTRACTED", thinking_date: "2026-09-11", thinking_date_basis: "INFERRED_YEAR", previous_revision_id: "previous_synthetic", comparison_basis: "PREVIOUS_SYNCED_USER_SECTION", added_lines: ["Investigate demand"], removed_lines: ["Demand stable"], comparison_truncated: false, user_excerpt: "I want to investigate demand.", user_summary: "Investigate demand before changing judgment.", other_viewpoints: [{ speaker: "Quoted Analyst", summary: "An independent opinion." }], warnings: [] }] } });
    if (path.endsWith("/changes")) return json({ data: { subject_id: SUBJECT, baseline: null, items: [], coverage: {}, warning_codes: [], total: 0, offset: 0, has_more: false } });
    if (path.startsWith("/api/monitors")) return json({ dashboard: { ok: true, data: { items: [] } } });
    return json({ items: [] });
  });
  return { posts, reads };
}
async function open(page: Page) {
  await page.goto(`/research?subject_id=${SUBJECT}&section=quick-review`);
  await expect(page.getByText("Prior synthetic statement", { exact: true })).toBeVisible();
  const close = page.getByRole("button", { name: "Close Copilot Panel", exact: true });
  if (await close.isVisible()) await close.click();
}
test("Quick Review pins evidence, recovers same intent and never resubmits on reload", async ({ page }) => {
  const api = await mock(page, true); await open(page);
  await expect(page.getByText("Investigate demand before changing judgment.", { exact: true })).toBeVisible();
  await expect(page.getByText(/Some evidence is missing or unavailable/)).toBeVisible();
  await expect(page.getByText(/2 notes still need their own review/)).toBeVisible();
  expect(api.posts).toHaveLength(0);
  await page.getByRole("button", { name: "Confirm No Action", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry Same Review" })).toBeVisible();
  await page.getByRole("button", { name: "Retry Same Review" }).click();
  await expect(page.getByText(/Recorded NO_ACTION/)).toBeVisible();
  expect(api.posts).toHaveLength(2);
  expect(api.posts[1]).toEqual(api.posts[0]);
  expect(api.posts[0].baseline_decision_id).toBe("decision_synthetic_baseline");
  expect(api.posts[0].confirmed).toBe(true);
  await page.evaluate(() => window.dispatchEvent(new Event("tp-observation-data-changed")));
  await expect(page.getByText(/your current review remains pinned/)).toBeVisible();
  expect(api.reads.filter((path) => path.endsWith("/quick-review"))).toHaveLength(1);
  await page.reload(); await expect(page.getByRole("button", { name: "Confirm No Action", exact: true })).toBeVisible();
  expect(api.posts).toHaveLength(2);
});
test("Follow-up requires gap and future date; adjustment preserves quick draft and does not propose", async ({ page }) => {
  const api = await mock(page); await open(page);
  await page.getByRole("combobox", { name: /Review Action/ }).selectOption("defer");
  await expect(page.getByRole("button", { name: "Save Follow-up" })).toBeDisabled();
  await page.getByRole("textbox", { name: /Evidence Gap/ }).fill("Need fresh demand evidence.");
  await page.evaluate(() => window.dispatchEvent(new Event("tp-observation-data-changed")));
  await expect(page.getByText("Notes updated; your draft was kept. Review the refreshed context before confirming.")).toBeVisible();
  await expect(page.getByRole("textbox", { name: /Evidence Gap/ })).toHaveValue("Need fresh demand evidence.");
  expect(api.reads.filter((path) => path.endsWith("/quick-review"))).toHaveLength(2);
  await page.getByRole("button", { name: "Adjust Thesis", exact: true }).click();
  await expect(page.getByRole("region", { name: "Create Thesis", exact: true })).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new Event("tp-observation-data-changed")));
  await page.getByRole("button", { name: "Return to Quick Review" }).click();
  await expect(page.getByText(/your current review remains pinned/)).toBeVisible();
  expect(api.reads.filter((path) => path.endsWith("/quick-review"))).toHaveLength(2);
  await expect(page.getByRole("textbox", { name: /Evidence Gap/ })).toHaveValue("Need fresh demand evidence.");
  const future = new Date(Date.now() + 86400000); future.setMinutes(future.getMinutes() - future.getTimezoneOffset());
  await page.getByLabel(/Follow-up Date/).fill(future.toISOString().slice(0, 16));
  await page.getByRole("button", { name: "Save Follow-up" }).click();
  await expect(page.getByText(/Recorded Follow-up/)).toBeVisible();
  expect(api.posts).toHaveLength(1); expect(api.posts[0].action).toBe("defer");
});

test("latest USER thinking prefills only on request and never replaces an open draft", async ({ page }) => {
  const api = await mock(page); await open(page);
  await page.getByRole("button", { name: "Review Thinking in Thesis", exact: true }).click();
  await page.getByRole("button", { name: "Use Edited Draft", exact: true }).click();
  const editor = page.getByRole("region", { name: "Create Thesis", exact: true });
  await expect(editor).toBeVisible();
  const statement = editor.getByRole("textbox", { name: /Statement/ });
  await expect(statement).toHaveValue("Investigate demand before changing judgment.");
  expect(await editor.getByRole("textbox", { name: /Rationale/ }).inputValue()).toContain("note_revision_synthetic");
  await statement.fill("My edited working draft");
  await page.getByRole("button", { name: "Return to Quick Review" }).click();
  await page.getByRole("button", { name: "Review Thinking in Thesis", exact: true }).click();
  await page.getByRole("button", { name: "Use Edited Draft", exact: true }).click();
  await expect(page.getByText(/formal Thesis editor is already open/)).toBeVisible();
  await page.getByRole("button", { name: "Adjust Thesis", exact: true }).click();
  await expect(statement).toHaveValue("My edited working draft");
  expect(api.posts).toHaveLength(0);
});

test("Saved Quick draft restores on reload without a POST", async ({ page }) => {
  const api = await mock(page); await open(page);
  await page.getByRole("textbox", { name: /Review Rationale/ }).fill("Keep my hand-written review.");
  await page.reload();
  await expect(page.getByText(/Your saved Quick Review draft was restored/)).toBeVisible();
  await expect(page.getByRole("textbox", { name: /Review Rationale/ })).toHaveValue("Keep my hand-written review.");
  expect(api.posts).toHaveLength(0);
});
test("Unknown submission reload checks status and retries the exact saved key only explicitly", async ({ page }) => {
  const api = await mock(page, true); await open(page);
  await page.getByRole("button", { name: "Confirm No Action", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry Same Review" })).toBeVisible();
  const original = api.posts[0]; await page.reload();
  await expect(page.getByText(/No receipt was found/)).toBeVisible();
  expect(api.posts).toHaveLength(1);
  await expect(page.getByRole("textbox", { name: /Review Rationale/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Refresh Review Context" })).toBeDisabled();
  await page.getByRole("button", { name: "Retry Same Review" }).click();
  await expect(page.getByText(/Recorded NO_ACTION/)).toBeVisible();
  expect(api.posts[1]).toEqual(original);
});
test("Recovered receipt shows outcome without resubmitting", async ({ page }) => {
  const api = await mock(page, true, "RECORDED"); await open(page);
  await page.getByRole("button", { name: "Confirm No Action", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry Same Review" })).toBeVisible();
  await page.reload(); await expect(page.getByText(/Recorded NO_ACTION/)).toBeVisible();
  expect(api.posts).toHaveLength(1);
});
test("Thinking date and edited before-after draft open formal editor only on acceptance", async ({ page }) => {
  const api = await mock(page); await open(page);
  await expect(page.getByText(/Thinking date: 2026-09-11 \(year inferred\)/)).toBeVisible();
  await page.getByRole("button", { name: "Review Thinking in Thesis" }).click();
  await expect(page.getByRole("textbox", { name: /Proposed Statement/ })).toHaveValue("Investigate demand before changing judgment.");
  await page.getByRole("textbox", { name: /Proposed Statement/ }).fill("My edited synthetic statement.");
  await expect(page.getByRole("region", { name: "Create Thesis", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Use Edited Draft" }).click();
  const editor = page.getByRole("region", { name: "Create Thesis", exact: true });
  await expect(editor.getByRole("textbox", { name: /Statement/ })).toHaveValue("My edited synthetic statement.");
  expect(api.posts).toHaveLength(0);
});
