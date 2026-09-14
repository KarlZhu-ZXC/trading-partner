import { expect, test, type Page } from "@playwright/test";

const A = "case_019ff000-0000-7000-8000-000000000001";
const B = "case_019ff000-0000-7000-8000-000000000002";
const AT = "2026-09-14T10:00:00Z";
const source = { instrument_id: "equity:US:TEST", source: "sec_edgar", eps_diluted: "2.25", currency: "USD", unit: "USD/share", share_basis: "reported diluted EPS", period_start: "2025-01-01", period_end: "2025-12-31", filed_at: "2026-02-01T10:00:00Z", accession: "synthetic-sec-accession", filing_form: "10-K", as_of: AT, shares_outstanding: null, shares_period_end: null, warning_codes: ["SYNTHETIC_AS_REPORTED"] };
const assumptions = { normalization_factor: "1", normalization_step: "0.1", pe_multiple: "20", pe_step: "2", business_model: "operating_company", rationale: "Synthetic normalized earnings assumption" };
const snapshot = { method: "normalized_diluted_eps_pe_v1", normalized_eps: "2.25", per_share_value: "45", sensitivity: ["0.9", "1", "1.1"].flatMap((normalization_factor) => ["18", "20", "22"].map((pe_multiple) => ({ normalization_factor, pe_multiple, per_share_value: "45" }))), value_basis: "AS_REPORTED_DILUTED_EPS", enterprise_value: null, aggregate_equity_value: null, source, assumptions, warnings: ["USER_ASSUMPTIONS_NOT_CONFIRMED_JUDGMENT"] };
const oldVersion = { version_id: "journal_synthetic_prior", created_at: AT, supersedes_version_id: null as string | null, snapshot };

function calibration(subjectId: string) {
  return { subject_id: subjectId, as_of: AT,
    baseline: subjectId === B ? null : { decision_id: "decision_synthetic_wait", decision_type: "no_action", title: "Wait for evidence", recorded_at: AT, thesis_revision_ids: ["rev_synthetic_exact"], trade_plan_id: null, trade_plan_version: null, review_due_at: "2099-01-01T00:00:00Z" },
    review_status: subjectId === B ? "NO_BASELINE" : "NOT_DUE",
    dimensions: [["FACTUAL_OUTCOME", "事实预测与结果"], ["PLAN_CONDITIONS", "计划条件观察"], ["ADHERENCE", "执行纪律"], ["ATTRIBUTABLE_TRADE_OUTCOME", "可归因交易结果"]].map(([code, title]) => ({ code, title, status: "UNKNOWN", summary: "未知不等于失败；未交易不是错误。", cards: [], observations: [], limitation_codes: [code === "ATTRIBUTABLE_TRADE_OUTCOME" ? "ATTRIBUTION_NOT_AVAILABLE" : "NO_ELIGIBLE_EVIDENCE"] })),
    coverage: { SCORECARD: "COMPLETE", AGENDA: "COMPLETE", RETRO: "COMPLETE" }, warning_codes: subjectId === B ? ["NO_REVIEWED_BASELINE"] : ["NO_MATCHING_SCORECARD", "REVIEW_NOT_DUE"], execution_effect: false };
}

async function mock(page: Page, delayedSource = false) {
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const reads: string[] = [];
  const versions = [oldVersion];
  let release: (() => void) | undefined;
  const blocked = delayedSource ? new Promise<void>((resolve) => { release = resolve; }) : null;
  await page.route("**/api/console/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/console", "");
    const json = (data: unknown) => route.fulfill({ json: data });
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      posts.push({ path, body });
      if (path === `/api/research/${A}/valuation/source`) { if (blocked) await blocked; return json({ data: { source_token: "source_synthetic", source, expires_at: "2099-01-01T00:00:00Z" } }); }
      if (path.endsWith("/valuation/calculate")) return json({ data: { ...snapshot, assumptions: body.assumptions } });
      if (path.endsWith("/valuation/versions")) {
        const saved = { ...oldVersion, version_id: "journal_synthetic_saved", supersedes_version_id: body.supersedes_version_id as string | null };
        versions.unshift(saved); return json({ data: saved });
      }
      return route.fulfill({ status: 403, json: { detail: "Unexpected synthetic write" } });
    }
    reads.push(path);
    if (path === "/api/session") return json({ token: "synthetic-session-token-0000000000000" });
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
    if (path === "/api/research") return json({ subjects: [A, B].map((id) => ({ subject: { subject_id: id, title: id === A ? "First Synthetic Subject" : "Second Synthetic Subject", subject_type: "company", summary: "Synthetic scope", status: "active", primary_instrument_id: "equity:US:TEST", topic_tags: [], linked_subject_ids: [], created_at: AT, updated_at: AT }, state: { ok: true, data: { theses: [], latest_revisions: [], assumptions: [], invalidations: [], open_questions: [], watchlist_items: [], pending_candidates: [], trade_plan_versions: [] } } })) });
    if (path.endsWith("/valuation")) return json({ data: { items: path.includes(A) ? versions : [], version_identity: "immutable journal_id; revisions may branch" } });
    if (path.endsWith("/calibration")) return json({ data: calibration(path.includes(A) ? A : B) });
    if (path.endsWith("/changes")) return json({ data: { subject_id: path.includes(A) ? A : B, as_of: AT, baseline: null, coverage: {}, warning_codes: ["NO_REVIEWED_BASELINE"], total: 0, offset: 0, limit: 25, has_more: false, items: [] } });
    if (path.startsWith("/api/monitors")) return json({ dashboard: { ok: true, data: { items: [] } } });
    return json({});
  });
  return { posts, reads, release: () => release?.() };
}

async function openValuation(page: Page) {
  await page.goto(`/research#subject-${A}`);
  await page.getByRole("tab", { name: "Valuation", exact: true }).click();
  return page.locator("#research-panel-valuation");
}

async function fillAssumptions(page: Page) {
  const panel = page.locator("#research-panel-valuation");
  await panel.getByLabel("Business Model").selectOption("operating_company");
  for (const [label, value] of [["EPS Normalization Factor", "1"], ["Normalization Sensitivity Step", "0.1"], ["P/E Multiple", "20"], ["P/E Sensitivity Step", "2"], ["Assumption Rationale", assumptions.rationale]]) await panel.getByLabel(label).fill(value);
}

test("valuation separates explicit source, string assumptions, compute, and reviewed save", async ({ page }) => {
  const api = await mock(page);
  await page.goto(`/research#subject-${A}`);
  await expect(page.getByRole("tab", { name: "Valuation", exact: true })).toBeVisible();
  expect(api.posts).toHaveLength(0);
  expect(api.reads.some((path) => path.endsWith("/valuation"))).toBe(false);
  await page.getByRole("tab", { name: "Valuation", exact: true }).click();
  const panel = page.locator("#research-panel-valuation");
  await expect(panel.getByText("No source loaded. Retrieval occurs only when requested.")).toBeVisible();
  expect(api.posts).toHaveLength(0);
  await expect(panel.getByLabel("P/E Multiple")).toHaveValue("");
  await panel.getByRole("button", { name: "Retrieve SEC Annual EPS", exact: true }).click();
  await expect(panel.getByText("synthetic-sec-accession", { exact: true }).first()).toBeVisible();
  await expect(panel.getByText("reported diluted EPS", { exact: false }).first()).toBeVisible();
  await fillAssumptions(page);
  await panel.getByRole("button", { name: "Calculate Scenarios", exact: true }).click();
  const computed = panel.locator("summary").filter({ hasText: "Computed Scenarios" }).locator("xpath=..");
  await expect(computed.locator("tbody tr")).toHaveCount(9);
  expect(api.posts.find((request) => request.path.endsWith("/calculate"))?.body.assumptions).toEqual(assumptions);
  await expect(panel.getByRole("button", { name: "Save Valuation Version", exact: true })).toBeDisabled();
  await panel.getByLabel("Save Authorization Note").fill("Save these synthetic assumptions only");
  await expect(panel.getByRole("button", { name: "Save Valuation Version", exact: true })).toBeDisabled();
  await panel.getByRole("checkbox", { name: /I reviewed these source facts/ }).check();
  await panel.getByRole("button", { name: "Save Valuation Version", exact: true }).click();
  await expect(panel.getByText(/Saved an immutable valuation version/)).toBeVisible();
  const saved = api.posts.find((request) => request.path.endsWith("/versions"));
  expect(saved?.body.confirmed).toBe(true);
  expect(saved?.body.supersedes_version_id).toBeNull();
  expect(saved?.body.idempotency_key).toEqual(expect.any(String));
  expect(api.posts.map((request) => request.path.split("/").at(-1))).toEqual(["source", "calculate", "versions"]);
});

test("saved assumption restore pins exact prior version and switching subjects clears it", async ({ page }) => {
  const api = await mock(page); const panel = await openValuation(page);
  const previous = panel.locator("summary").filter({ hasText: oldVersion.version_id }).locator("xpath=..");
  await previous.locator("summary").first().click();
  await previous.getByRole("button", { name: "Restore Assumptions From This Version" }).click();
  await expect(panel.getByLabel("P/E Multiple")).toHaveValue("20");
  await expect(panel.getByText(`New version will supersede exactly ${oldVersion.version_id}.`)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Calculate Scenarios", exact: true })).toBeDisabled();
  expect(api.posts).toHaveLength(0);
  await panel.getByRole("button", { name: "Retrieve SEC Annual EPS", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Calculate Scenarios", exact: true })).toBeEnabled();
  await panel.getByRole("button", { name: "Calculate Scenarios", exact: true }).click();
  await panel.getByLabel("Save Authorization Note").fill("Append to exact restored version");
  await panel.getByRole("checkbox", { name: /I reviewed these source facts/ }).check();
  await panel.getByRole("button", { name: "Save Valuation Version", exact: true }).click();
  await expect.poll(() => api.posts.find((request) => request.path.endsWith("/versions"))?.body.supersedes_version_id).toBe(oldVersion.version_id);
  await page.getByRole("option", { name: /Second Synthetic Subject/ }).click();
  await page.getByRole("tab", { name: "Valuation", exact: true }).click();
  await expect(panel.getByLabel("P/E Multiple")).toHaveValue("");
  await expect(panel.getByText("No source loaded. Retrieval occurs only when requested.")).toBeVisible();
  await expect(panel.getByText(oldVersion.version_id, { exact: true })).toHaveCount(0);
});

test("late source response cannot populate another subject", async ({ page }) => {
  const api = await mock(page, true); const panel = await openValuation(page);
  await panel.getByRole("button", { name: "Retrieve SEC Annual EPS", exact: true }).click();
  await expect.poll(() => api.posts.length).toBe(1);
  await page.getByRole("option", { name: /Second Synthetic Subject/ }).click();
  await page.getByRole("tab", { name: "Valuation", exact: true }).click();
  api.release();
  await expect(panel.getByText("No source loaded. Retrieval occurs only when requested.")).toBeVisible();
  await expect(panel.getByText("synthetic-sec-accession", { exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Calculate Scenarios", exact: true })).toBeDisabled();
});

test("calibration keeps not-due NO_ACTION and missing baseline separate from failure verdicts", async ({ page }) => {
  const api = await mock(page); await page.goto(`/research#subject-${A}`);
  await page.getByRole("tab", { name: "Calibration", exact: true }).click();
  const panel = page.locator("#research-panel-calibration");
  await expect(panel.getByText("NOT_DUE", { exact: true })).toBeVisible();
  await expect(panel.getByText("Wait for evidence · no_action", { exact: true })).toBeVisible();
  await expect(panel.getByRole("link", { name: "Open Scorecards" })).toHaveAttribute("href", `/scorecards?subject_id=${A}`);
  for (const title of ["事实预测与结果", "计划条件观察", "执行纪律", "可归因交易结果"]) await panel.locator("summary").filter({ hasText: title }).click();
  await expect(panel.getByText("UNKNOWN", { exact: true })).toHaveCount(4);
  await expect(panel.getByText("ATTRIBUTION_NOT_AVAILABLE", { exact: true })).toBeVisible();
  await expect(panel.getByText(/^(FAILED|PASS|WIN|LOSS)$/)).toHaveCount(0);
  await page.getByRole("option", { name: /Second Synthetic Subject/ }).click();
  await page.getByRole("tab", { name: "Calibration", exact: true }).click();
  await expect(panel.getByText(/No user-reviewed Decision yet/)).toBeVisible();
  await expect(panel.getByText("NOT_DUE", { exact: true })).toHaveCount(0);
  expect(api.posts).toHaveLength(0);
});
