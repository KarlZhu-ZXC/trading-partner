import { expect, test, type Page, type Route } from "@playwright/test";

const SUBJECT_ID = "case_019ff000-0000-7000-8000-000000000101";
const THESIS_ID = "thesis_019ff000-0000-7000-8000-000000000102";
const REVISION_ID = "revision_019ff000-0000-7000-8000-000000000103";
const ASSUMPTION_ID = "assumption_019ff000-0000-7000-8000-000000000104";
const IMPACT_ID = "impact_019ff000-0000-7000-8000-000000000105";
const QUESTION_ID = "question_019ff000-0000-7000-8000-000000000106";
const SOURCE_ID = "evidence_019ff000-0000-7000-8000-000000000107";
const DECISION_ID = "decision_019ff000-0000-7000-8000-000000000108";
const AT = "2026-09-20T10:00:00Z";

const source = {
  source: { kind: "EVIDENCE", source_id: SOURCE_ID },
  title: "Synthetic revenue guidance",
  fact_at: AT,
  recorded_at: AT,
  origin: "Synthetic fixture",
  warning_codes: [],
};

const target = {
  thesis_id: THESIS_ID,
  thesis_revision_id: REVISION_ID,
  revision_no: 4,
  thesis_title: "Demand remains resilient",
  assumption_id: ASSUMPTION_ID,
  statement: "Gross margin remains above the planning threshold.",
  is_current: true,
};

const openQuestion = {
  subject_id: SUBJECT_ID,
  question_id: QUESTION_ID,
  text: "Will the next report confirm the margin assumption?",
  status: "OPEN",
  version: 3,
  evidence_requirements: ["A reported gross margin above 40%"],
  due_at: "2026-10-01T23:59:59Z",
  progress_note: "Waiting for the next report.",
  source_refs: [],
  decision_ids: [],
};

function researchState(includeCandidate = true) {
  return {
    subjects: [{
      subject: {
        subject_id: SUBJECT_ID,
        subject_type: "company",
        title: "Synthetic Evidence Subject",
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
          theses: [{ thesis_id: THESIS_ID, title: "Demand remains resilient", status: "active", role: "primary", current_revision_no: 4 }],
          latest_revisions: [{ thesis_id: THESIS_ID, revision_id: REVISION_ID, revision_no: 4, statement: target.statement }],
          assumptions: [{ thesis_id: THESIS_ID, assumption_id: ASSUMPTION_ID, statement: target.statement, revision_no: 4 }],
          invalidations: [],
          open_questions: [openQuestion],
          watchlist_items: [],
          pending_candidates: includeCandidate ? [{
            candidate_id: "run_019ff000-0000-7000-8000-000000000109",
            subject_id: SUBJECT_ID,
            kind: "evidence_impact",
            status: "proposed",
            proposed_at: AT,
            expires_at: "2026-10-01T10:00:00Z",
            proposed_by: "user",
            proposed_by_rationale: "Synthetic candidate rationale",
            confirmation_mode: "strict_review",
            payload: {
              kind: "evidence_impact",
              action: "create",
              thesis_revision_id: REVISION_ID,
              assumption_id: ASSUMPTION_ID,
              source: { kind: "EVIDENCE", source_id: SOURCE_ID },
              stance: "supports",
              rationale: "The report supports the margin premise.",
            },
          }] : [],
          trade_plan_versions: [],
        },
      },
    }],
  };
}

async function mockResearch(page: Page, options: { sourceFailure?: boolean; writeFailure?: boolean } = {}) {
  const writes: Array<{ path: string; body: Record<string, unknown> }> = [];
  await page.route("**/api/console/api/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (path === "/api/session") return json({ token: "synthetic-session-token-0000000000000000" });
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
    if (path === "/api/research") return json(researchState());
    if (path === `/api/research/${SUBJECT_ID}/evidence-links`) {
      if (options.sourceFailure) return json({ detail: "SYNTHETIC_SOURCE_FAILURE" }, 500);
      return json({ data: {
        subject_id: SUBJECT_ID,
        as_of: AT,
        sources: [source],
        sources_has_more: false,
        impacts: [{ impact_id: IMPACT_ID, version: 2, subject_id: SUBJECT_ID, thesis_id: THESIS_ID, thesis_revision_id: REVISION_ID, assumption_id: ASSUMPTION_ID, source: source.source, stance: "supports", rationale: "The report supports the margin premise.", state: "ACTIVE", confirmed_at: AT, confirmed_by: "user", candidate_id: null, source_available: true }],
        impacts_has_more: false,
        targets: [target],
        decisions: [{ decision_id: DECISION_ID, decided_at: AT, title: "Wait for margin confirmation" }],
        coverage: { sources: "COMPLETE", impacts: "COMPLETE" },
      } });
    }
    if (request.method() === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      writes.push({ path, body });
      if (options.writeFailure) return json({ result: { ok: false, errors: [{ code: "VERSION_CONFLICT", message: "Expected question version is stale." }] } });
      return json({ result: { ok: true, data: { candidate_id: "run_synthetic_candidate" } } });
    }
    if (path.startsWith("/api/monitors")) return json({ dashboard: { ok: true, data: { items: [] } } });
    return json({});
  });
  return { writes };
}

test("Research load and evidence inspection do not write", async ({ page }) => {
  const api = await mockResearch(page);
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  await expect(page.locator("#research-panel-overview").getByText("Synthetic revenue guidance", { exact: true })).toBeVisible();
  await expect(page.getByText(SOURCE_ID, { exact: true }).first()).toBeVisible();
  expect(api.writes).toEqual([]);
});

test("creating an evidence impact proposes one exact Candidate and never confirms it", async ({ page }) => {
  const api = await mockResearch(page);
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  await page.getByRole("tab", { name: "Evidence", exact: true }).click();
  const panel = page.locator("#research-section-evidence-impact");
  await panel.getByRole("button", { name: "Link Evidence to Thesis" }).click();
  await panel.getByLabel("Thesis Revision and Assumption").selectOption({ label: "Demand remains resilient · v4 · current · Gross margin remains above the planning threshold." });
  await panel.getByLabel("Evidence Source").selectOption({ label: "Synthetic revenue guidance · EVIDENCE · evidence_019ff000-0000-7000-8000-000000000107" });
  await panel.getByLabel("Rationale").fill("The reported margin supports the assumption.");
  await panel.getByRole("button", { name: "Propose Candidate" }).click();
  await expect(panel.getByText("Candidate proposal created", { exact: false })).toBeVisible();
  expect(api.writes).toHaveLength(1);
  const request = api.writes[0];
  expect(request.path).toBe("/api/tools/invoke");
  expect(request.body.tool_name).toBe("research_judgment_propose");
  expect((request.body.arguments as Record<string, unknown>).request).toMatchObject({
    operation: "research_state",
    case_id: SUBJECT_ID,
    payload: {
      kind: "evidence_impact",
      action: "create",
      thesis_revision_id: REVISION_ID,
      assumption_id: ASSUMPTION_ID,
      source: { kind: "EVIDENCE", source_id: SOURCE_ID },
      stance: "uncertain",
      rationale: "The reported margin supports the assumption.",
    },
    proposed_by: "user",
  });
  expect(request.body.confirmation).toBe("research_judgment_propose");
  expect(api.writes.some((item) => item.body.tool_name === "research_judgment_confirm")).toBe(false);
});

test("pending evidence Candidate shows exact source, pinned revision, assumption, stance, and action", async ({ page }) => {
  await mockResearch(page);
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  const candidate = page.locator(".research-candidate").first();
  await expect(candidate).toContainText("Evidence Impact · CREATE");
  await expect(candidate).toContainText(SOURCE_ID);
  await expect(candidate).toContainText(REVISION_ID);
  await expect(candidate).toContainText(ASSUMPTION_ID);
  await expect(candidate).toContainText("SUPPORTS");
});

test("answering a tracked question requires criteria and source, while source failure blocks submit", async ({ page }) => {
  const api = await mockResearch(page);
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  await page.getByRole("tab", { name: "Thesis", exact: true }).click();
  const panel = page.locator("#research-section-questions");
  await panel.getByRole("button", { name: "Answer", exact: true }).click();
  await panel.getByLabel("Answer Summary").fill("The next report confirms the required margin.");
  await panel.getByRole("button", { name: "Propose Candidate" }).click();
  await expect(panel.getByRole("alert")).toContainText("criteria");
  await panel.locator("input[type=checkbox]").first().check();
  await panel.getByText(/I reviewed the stated evidence requirements/).locator("input").check();
  await panel.getByRole("button", { name: "Propose Candidate" }).click();
  await expect(panel.getByText("Candidate proposal created", { exact: false })).toBeVisible();
  expect(api.writes).toHaveLength(1);
  expect((api.writes[0].body.arguments as Record<string, unknown>).request).toMatchObject({ payload: { kind: "open_question", action: "answer", question_id: QUESTION_ID, expected_version: 3, criteria_met: true, source_refs: [{ kind: "EVIDENCE", source_id: SOURCE_ID }] } });

  const failedPage = await page.context().newPage();
  await mockResearch(failedPage, { sourceFailure: true });
  await failedPage.goto(`/research#subject-${SUBJECT_ID}`);
  await failedPage.getByRole("tab", { name: "Thesis", exact: true }).click();
  const failedPanel = failedPage.locator("#research-section-questions");
  await expect(failedPanel.getByRole("alert")).toContainText("Source and Decision choices could not be read");
  await failedPanel.getByRole("button", { name: "Answer", exact: true }).click();
  await expect(failedPanel.getByText("writes are blocked", { exact: false })).toBeVisible();
  await expect(failedPanel.getByRole("button", { name: "Propose Candidate" })).toBeDisabled();
  await failedPage.close();
});

test("question version conflict preserves the answer draft", async ({ page }) => {
  await mockResearch(page, { writeFailure: true });
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  await page.getByRole("tab", { name: "Thesis", exact: true }).click();
  const panel = page.locator("#research-section-questions");
  await panel.getByRole("button", { name: "Answer", exact: true }).click();
  await panel.getByLabel("Answer Summary").fill("Keep this draft after a version conflict.");
  await panel.locator("input[type=checkbox]").first().check();
  await panel.getByText(/I reviewed the stated evidence requirements/).locator("input").check();
  await panel.getByRole("button", { name: "Propose Candidate" }).click();
  await expect(panel.getByRole("alert")).toContainText("VERSION_CONFLICT");
  await expect(panel.getByLabel("Answer Summary")).toHaveValue("Keep this draft after a version conflict.");
});
