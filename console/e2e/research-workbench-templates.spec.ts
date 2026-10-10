import { expect, test, type Page, type Route } from "@playwright/test";

const SUBJECT_ID = "case_019ff000-0000-7000-8000-000000000201";
const THESIS_ID = "thesis_019ff000-0000-7000-8000-000000000202";
const REVISION_ID = "revision_019ff000-0000-7000-8000-000000000203";
const ASSUMPTION_ID = "assumption_019ff000-0000-7000-8000-000000000204";
const SECOND_ASSUMPTION_ID = "assumption_019ff000-0000-7000-8000-000000000205";
const SUPPORT_SOURCE_ID = "evidence_019ff000-0000-7000-8000-000000000206-very-long-reference-for-mobile-layout";
const CONTRARY_SOURCE_ID = "monitor-run-019ff000-0000-7000-8000-000000000207";
const AT = "2026-09-20T10:00:00Z";

const target = {
  thesis_id: THESIS_ID,
  thesis_revision_id: REVISION_ID,
  revision_no: 4,
  thesis_title: "Demand remains resilient",
  assumption_id: ASSUMPTION_ID,
  statement: "Gross margin remains above the planning threshold.",
  is_current: true,
};

const historicalTarget = {
  ...target,
  assumption_id: SECOND_ASSUMPTION_ID,
  statement: "Historical margin premise had a wider range.",
  is_current: false,
};

const source = {
  source: { kind: "EVIDENCE", source_id: SUPPORT_SOURCE_ID },
  title: "Synthetic revenue guidance",
  source_name: "Official Earnings Filing",
  source_url: "https://example.test/filings/revenue-guidance",
  fact_at: AT,
  recorded_at: AT,
  origin: "Synthetic fixture",
  warning_codes: ["SYNTHETIC_SOURCE"],
};

function state() {
  return {
    subjects: [{
      subject: {
        subject_id: SUBJECT_ID,
        subject_type: "company",
        title: "Synthetic Workbench Subject",
        summary: "Synthetic research scope.",
        primary_instrument_id: "equity:US:SYNTH",
        status: "active",
        topic_tags: [],
        linked_subject_ids: [],
        created_at: AT,
        updated_at: AT,
      },
      state: {
        ok: true,
        data: {
          theses: [{ thesis_id: THESIS_ID, title: target.thesis_title, status: "active", role: "primary", current_revision_no: 4 }],
          latest_revisions: [{ thesis_id: THESIS_ID, revision_id: REVISION_ID, revision_no: 4, statement: target.statement }],
          assumptions: [{ thesis_id: THESIS_ID, assumption_id: ASSUMPTION_ID, statement: target.statement, revision_no: 4 }],
          invalidations: [],
          open_questions: [],
          watchlist_items: [],
          pending_candidates: [],
          trade_plan_versions: [],
        },
      },
    }],
  };
}

async function mockResearch(page: Page, { sourceFailure = false } = {}) {
  const writes: unknown[] = [];
  await page.route("**/api/console/api/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (path === "/api/session") return json({ token: "synthetic-session-token-0000000000000000" });
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
    if (path === "/api/research") return json(state());
    if (path === `/api/research/${SUBJECT_ID}/evidence-links`) {
      if (sourceFailure) return json({ detail: "SYNTHETIC_SOURCE_FAILURE" }, 500);
      return json({ data: {
        subject_id: SUBJECT_ID,
        as_of: AT,
        sources: [source],
        sources_has_more: false,
        impacts: [
          { impact_id: "impact_support", version: 1, subject_id: SUBJECT_ID, thesis_id: THESIS_ID, thesis_revision_id: REVISION_ID, assumption_id: ASSUMPTION_ID, source: source.source, stance: "supports", rationale: "The filing supports the margin premise.", state: "ACTIVE", confirmed_at: AT, confirmed_by: "reviewer-a", candidate_id: null, source_available: true },
          { impact_id: "impact_contradiction", version: 2, subject_id: SUBJECT_ID, thesis_id: THESIS_ID, thesis_revision_id: REVISION_ID, assumption_id: SECOND_ASSUMPTION_ID, source: { kind: "MONITOR", source_id: CONTRARY_SOURCE_ID, monitor_id: "monitor_019ff000-0000-7000-8000-000000000208", rule_code: "MARGIN_BELOW_PLAN" }, stance: "contradicts", rationale: "The monitor recorded a counter-signal.", state: "ACTIVE", confirmed_at: AT, confirmed_by: "reviewer-b", candidate_id: null, source_available: false },
          { impact_id: "impact_unknown", version: 1, subject_id: SUBJECT_ID, thesis_id: THESIS_ID, thesis_revision_id: REVISION_ID, assumption_id: ASSUMPTION_ID, source: source.source, stance: "unclassified", rationale: "The stored stance is not one of the known labels.", state: "ACTIVE", confirmed_at: AT, confirmed_by: null, candidate_id: null, source_available: true },
        ],
        impacts_has_more: false,
        targets: [target, historicalTarget],
        decisions: [],
        impact_history: [],
        coverage: { sources: "COMPLETE", impacts: "COMPLETE", impact_history: "COMPLETE" },
      } });
    }
    if (request.method() === "POST") {
      writes.push(request.postDataJSON());
      return json({ result: { ok: true, data: { candidate_id: "run_synthetic_candidate" } } });
    }
    if (path.startsWith("/api/monitors")) return json({ dashboard: { ok: true, data: { items: [] } } });
    return json({});
  });
  return writes;
}

test("evidence comparison keeps exact targets, lanes, provenance, and unavailable historical refs", async ({ page }) => {
  const writes = await mockResearch(page);
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  await page.getByRole("tab", { name: "Evidence", exact: true }).click();
  const workbench = page.getByRole("region", { name: "Evidence Comparison" });
  await expect(workbench).toBeVisible();
  await expect(workbench.getByText("Supports", { exact: true })).toBeVisible();
  await expect(workbench.getByText("Contradicts", { exact: true })).toBeVisible();
  await expect(workbench.getByText("Uncertain", { exact: true })).toBeVisible();
  await expect(workbench.getByText("Synthetic revenue guidance", { exact: true }).first()).toBeVisible();
  await expect(workbench).toContainText("monitor_id=monitor_019ff000-0000-7000-8000-000000000208");
  await expect(workbench).toContainText("Source metadata is currently unavailable");
  await workbench.getByText("Source Details", { exact: true }).first().click();
  await expect(workbench).toContainText("SYNTHETIC_SOURCE");
  await expect(workbench.getByText("Official Earnings Filing", { exact: true }).first()).toBeVisible();
  const external = workbench.getByRole("link", { name: "Synthetic revenue guidance" });
  await expect(external).toHaveAttribute("href", "https://example.test/filings/revenue-guidance");
  await expect(external).toHaveAttribute("rel", "noopener noreferrer");
  const filter = workbench.getByLabel("Exact Thesis Revision + Assumption Filter");
  await expect(filter).toHaveValue("");
  await filter.selectOption({ label: "Demand remains resilient · v4 · current" });
  await expect(workbench.getByText("No active links in this lane.", { exact: true })).toHaveCount(2);
  await expect(workbench.getByText("Stance unavailable", { exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});

test("question templates require an explicit replacement and do not write a Candidate", async ({ page }) => {
  const writes = await mockResearch(page);
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  await page.getByRole("tab", { name: "Thesis", exact: true }).click();
  const panel = page.locator("#research-section-questions");
  await panel.getByRole("button", { name: "Add Research Question", exact: true }).click();
  await panel.getByRole("textbox", { name: "Question", exact: true }).fill("Custom draft I wrote first.");
  await panel.getByLabel("Question Template", { exact: true }).selectOption("earnings_review");
  await expect(panel.getByRole("button", { name: "Replace Draft with Template", exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Replace Draft with Template", exact: true }).click();
  await expect(panel.getByRole("textbox", { name: "Question", exact: true })).toHaveValue("What did the latest earnings report change about the assumptions behind the active Thesis?");
  await expect(panel.getByLabel("Evidence Requirements")).toContainText("Latest reported revenue");
  await expect(panel.getByLabel("Deadline")).toHaveValue("");
  await expect(panel.getByRole("button", { name: "Propose Candidate", exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});

test("mobile evidence workbench keeps long references contained when source read fails", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockResearch(page, { sourceFailure: true });
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  await page.getByRole("tab", { name: "Evidence", exact: true }).click();
  await expect(page.getByRole("alert").first()).toContainText("Evidence links could not be read");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
