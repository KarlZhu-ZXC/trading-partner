import { expect, test, type Page } from "@playwright/test";

const A = "case_019ff000-0000-7000-8000-000000000001";
const B = "case_019ff000-0000-7000-8000-000000000002";
const REVIEW = "decision_019ff000-0000-7000-8000-000000000003";
const CHANGE = "change_019ff000-0000-7000-8000-000000000004";
const REVISION = "external_note_revision_019ff000-0000-7000-8000-000000000005";
const AT = "2026-09-14T10:00:00Z";

function response(subjectId: string, partial = false) {
  return { data: {
    subject_id: subjectId, as_of: AT,
    baseline: { decision_id: subjectId === A ? REVIEW : "decision_other", title: "Reviewed demand outlook", decided_at: AT, recorded_at: AT,
      theses: [{ thesis_id: "thesis_synthetic", revision_id: "revision_synthetic", statement: "Demand remains resilient at the reviewed margin." }], plan: null },
    coverage: { observations: "COMPLETE", monitors: partial ? "UNAVAILABLE" : "COMPLETE", agenda: "NOT_APPLICABLE" },
    warning_codes: partial ? ["MONITOR_READ_FAILED"] : [], total: 1, offset: 0, limit: 25, has_more: false,
    items: [{ change_id: subjectId === A ? CHANGE : "change_other", kind: "OBSERVATION", title: subjectId === A ? "Demand note changed" : "Other subject change",
      occurred_at: AT, recorded_at: AT, instrument_id: "equity:US:TEST", source_id: "note_synthetic", source_version: 2,
      old_value: "Demand stable", new_value: "Demand weakening", relation: "SUBJECT_SCOPE", relation_detail: "Same Research Subject; no exact assumption link.",
      thesis_id: null, plan_id: null, plan_version: null, note_revision_id: REVISION, monitor_id: null, event_id: null, agenda_item_id: null, warning_codes: [] }],
  } };
}

async function mock(page: Page, options: { partial?: boolean; delay?: boolean; pages?: boolean; noBaseline?: boolean; fail?: boolean } = {}) {
  const requests: URL[] = [];
  const writes: string[] = [];
  const reads: string[] = [];
  let partial = options.partial ?? false;
  let fail = options.fail ?? false;
  let release: (() => void) | undefined;
  const blocked = options.delay ? new Promise<void>((resolve) => { release = resolve; }) : null;
  await page.route("**/api/console/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/console", "");
    const json = (value: unknown) => route.fulfill({ json: value });
    if (route.request().method() === "POST") { writes.push(path); return route.fulfill({ status: 403, json: { detail: "Unexpected synthetic write" } }); }
    reads.push(path);
    const exactNote = { identity: { note_id: "note_synthetic", title: "Historical Synthetic Note", primary_instrument_id: "equity:US:TEST" }, revision: { note_revision_id: REVISION, version: 2, coverage: "FULL", observed_at: AT, summary: "Historical revision two", blocks: [] }, interpretation: null, review: { status: "PENDING", review_id: "review_synthetic", version: 1 } };
    const latestNote = { ...exactNote, identity: { ...exactNote.identity, title: "Latest Synthetic Note" }, revision: { ...exactNote.revision, note_revision_id: "external_note_revision_latest", version: 3, summary: "Latest revision three" } };
    const envelope = (data: object) => ({ ok: true, data, warnings: [], errors: [] });
    if (path === `/api/observations/${REVISION}/revision`) return json({ data: exactNote });
    if (path === "/api/observations") return json({ data: { external_notes: [latestNote], observation_sources: [] } });
    if (path === "/api/decision-workbench") return json({ selected_subject_id: A, subjects: [{ subject: { subject_id: A, title: "First Synthetic Subject", subject_type: "company", summary: "Synthetic scope", status: "ACTIVE", primary_instrument_id: "equity:US:TEST" }, state: envelope({ theses: [], latest_revisions: [], pending_candidates: [], open_questions: [], current_trade_plan: null }) }], partial_failures: [], accounts: envelope({ accounts: [] }), transactions: envelope({ transactions: [], history_complete: true }), trade_cycles: envelope({ cycles: [], status: "INCOMPLETE" }), external_notes: [latestNote] });
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
    if (path === "/api/session") return json({ token: "synthetic-session-token-0000000000000" });
    if (path === "/api/research") return json({ subjects: [A, B].map((id) => ({ subject: {
      subject_id: id, title: id === A ? "First Synthetic Subject" : "Second Synthetic Subject", subject_type: "company", summary: "Synthetic scope", status: "active", primary_instrument_id: "equity:US:TEST", topic_tags: [], linked_subject_ids: [], created_at: AT, updated_at: AT,
    }, state: { ok: true, data: { theses: [], latest_revisions: [], assumptions: [], invalidations: [], open_questions: [], watchlist_items: [], pending_candidates: [], trade_plan_versions: [] } } })) });
    if (path.endsWith("/changes")) {
      requests.push(url);
      const id = path.split("/")[3];
      if (blocked && id === A) await blocked;
      if (fail) return route.fulfill({ status: 500, json: { error: "SYNTHETIC_READ_FAILED" } });
      const payload = response(id, partial);
      if (options.pages) {
        const offset = Number(url.searchParams.get("offset") ?? "0");
        payload.data.offset = offset;
        payload.data.total = 26;
        payload.data.has_more = offset === 0;
        payload.data.items = offset === 0 ? Array.from({ length: 25 }, (_, index) => ({ ...payload.data.items[0], change_id: `change_page_${index}`, title: `Synthetic Change ${index + 1}` })) : payload.data.items;
      }
      if (options.noBaseline) return json({ data: { ...payload.data, baseline: null, total: 0, items: [] } });
      return json(payload);
    }
    if (path.startsWith("/api/monitors")) return json({ dashboard: { ok: true, data: { items: [] } } });
    return json({});
  });
  return { requests, writes, reads, recover: () => { partial = false; fail = false; }, release: () => release?.() };
}

test("review context pins exact baseline and selection through refresh and reload", async ({ page }) => {
  const api = await mock(page);
  await page.goto(`/research?keep=opaque#subject-${A}`);
  const panel = page.locator("#research-changes");
  await expect(panel.getByText("Demand remains resilient at the reviewed margin.")).toBeVisible();
  await panel.getByRole("button", { name: /Demand note changed/ }).click();
  await expect(panel.getByText("Demand stable", { exact: true })).toBeVisible();
  await expect(panel.getByText("Demand weakening", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`changes_baseline=${REVIEW}.*change_id=${CHANGE}#subject-${A}`));
  await expect(panel.getByRole("link", { name: "Review This Change" })).toHaveAttribute("href", `/decision-workbench?subject_id=${A}&change_id=${CHANGE}&changes_baseline=${REVIEW}&note_revision_id=${REVISION}#notes`);
  await panel.getByRole("button", { name: "Refresh Changes" }).click();
  await expect.poll(() => api.requests.at(-1)?.searchParams.get("baseline_decision_id")).toBe(REVIEW);
  await page.reload();
  await expect(panel.getByText("Demand weakening", { exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("keep")).toBe("opaque");
  await panel.getByRole("button", { name: "Use Latest Review" }).click();
  await expect.poll(() => api.requests.at(-1)?.searchParams.has("baseline_decision_id")).toBe(false);
  await expect(panel.getByText("Demand weakening", { exact: true })).toHaveCount(0);
});

test("partial coverage remains explicit and refresh retries failed sources", async ({ page }) => {
  const api = await mock(page, { partial: true });
  await page.goto(`/research#subject-${A}`);
  const panel = page.locator("#research-changes");
  await expect(panel.getByText(/Coverage is incomplete/)).toBeVisible();
  await expect(panel.getByText("MONITOR_READ_FAILED", { exact: true })).toBeVisible();
  api.recover();
  await panel.getByRole("button", { name: "Refresh Changes" }).click();
  await expect(panel.getByText(/Coverage is incomplete/)).toHaveCount(0);
  await expect(panel.getByRole("button", { name: /Demand note changed/ })).toBeVisible();
});

test("subject switch discards an in-flight response and clears incompatible pins", async ({ page }) => {
  const api = await mock(page, { delay: true });
  await page.goto(`/research?keep=opaque&changes_baseline=${REVIEW}&change_id=${CHANGE}&changes_offset=25#subject-${A}`);
  await expect.poll(() => api.requests.length).toBe(1);
  await page.getByRole("option", { name: /Second Synthetic Subject/ }).click();
  await expect(page.locator("#research-changes").getByRole("button", { name: /Other subject change/ })).toBeVisible();
  api.release();
  await expect(page.locator("#research-changes").getByRole("button", { name: /Demand note changed/ })).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("change_id")).toBeNull();
  expect(new URL(page.url()).searchParams.get("changes_offset")).toBeNull();
  expect(new URL(page.url()).searchParams.get("keep")).toBe("opaque");
  expect(api.requests.at(-1)?.searchParams.has("baseline_decision_id")).toBe(false);
  expect(api.requests.at(-1)?.searchParams.has("change_id")).toBe(false);
  expect(api.requests.at(-1)?.searchParams.get("offset")).toBe("0");
});


test("page and selected change restore together after reload", async ({ page }) => {
  const api = await mock(page, { pages: true });
  await page.goto(`/research#subject-${A}`);
  const panel = page.locator("#research-changes");
  await panel.getByRole("button", { name: "Next Changes" }).click();
  await panel.getByRole("button", { name: /Demand note changed/ }).click();
  await expect(panel.getByText("Demand weakening", { exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("changes_offset")).toBe("25");
  await expect(panel.getByRole("link", { name: "Review This Change" })).toHaveAttribute("href", new RegExp("changes_offset=25"));
  await page.reload();
  await expect(panel.getByText("Demand weakening", { exact: true })).toBeVisible();
  expect(api.requests.at(-1)?.searchParams.get("offset")).toBe("25");
  await expect(panel.getByRole("button", { name: "Next Changes" })).toBeDisabled();
});

test("no review is explicit and a failed read can be retried", async ({ page }) => {
  const api = await mock(page, { noBaseline: true, fail: true });
  await page.goto(`/research#subject-${A}`);
  const panel = page.locator("#research-changes");
  await expect(panel.getByText(/Changes could not be read/)).toBeVisible();
  await expect(panel.getByText(/No recorded changes/)).toHaveCount(0);
  api.recover();
  await panel.getByRole("button", { name: "Refresh Changes" }).click();
  await expect(panel.getByText(/No completed user review/)).toBeVisible();
  expect(new URL(page.url()).searchParams.get("changes_baseline")).toBe("none");
  await page.reload();
  await expect(panel.getByText(/No completed user review/)).toBeVisible();
  expect(api.requests.at(-1)?.searchParams.get("baseline_decision_id")).toBe("none");
});


test("Research review opens the exact historical Observation and returns after reload without writes", async ({ page }) => {
  const api = await mock(page, { pages: true });
  await page.goto(`/research#subject-${A}`);
  const panel = page.locator("#research-changes");
  await panel.getByRole("button", { name: "Next Changes" }).click();
  await panel.getByRole("button", { name: /Demand note changed/ }).click();
  await panel.getByRole("link", { name: "Review This Change" }).click();
  await expect(page.getByRole("region", { name: "Selected Note" }).getByRole("heading", { name: "Historical Synthetic Note" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Selected Note" })).toContainText("v2");
  await expect(page.getByText("Latest revision three", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("region", { name: "Selected Note" }).getByRole("heading", { name: "Historical Synthetic Note" })).toBeVisible();
  await page.getByRole("link", { name: "Return to Research Changes" }).click();
  await expect(panel.getByText("Demand weakening", { exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("changes_offset")).toBe("25");
  expect(new URL(page.url()).searchParams.get("changes_baseline")).toBe(REVIEW);
  expect(api.reads.filter((path) => path === `/api/observations/${REVISION}/revision`).length).toBeGreaterThanOrEqual(2);
  expect(api.writes).toEqual([]);
});
