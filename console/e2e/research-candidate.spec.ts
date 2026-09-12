import { expect, test, type Page, type Route } from "@playwright/test";

const SUBJECT_ID = "case_019ff000-0000-7000-8000-000000000001";
const CANDIDATE_ID = "run_019ff000-0000-7000-8000-000000000002";
const SOURCE_REVISION_ID = "external_note_revision_019ff000-0000-7000-8000-000000000004";
const CREATED_SUBJECT_ID = "case_019ff000-0000-7000-8000-000000000005";
const EXISTING_PLAN_SUBJECT_ID = "case_019ff000-0000-7000-8000-000000000008";
const SOURCE_TOKEN = SOURCE_REVISION_ID.replace(/[^a-zA-Z0-9]/g, "").slice(-12);

const candidate = {
  candidate_id: CANDIDATE_ID,
  subject_id: SUBJECT_ID,
  thesis_id: "thesis_019ff000-0000-7000-8000-000000000003",
  target_revision_no: 1,
  kind: "thesis_revision",
  confirmation_mode: "strict_review",
  status: "proposed",
  proposed_at: "2026-08-20T10:00:00Z",
  expires_at: "2026-08-27T10:00:00Z",
  proposed_by: "user",
  proposed_by_rationale: "New evidence requires a narrower judgment.",
  reviewed_at: null,
  reviewed_by: null,
  review_note: null,
  rejection_reason: null,
  idempotency_key: "candidate-e2e",
  payload: {
    kind: "thesis_revision",
    title: "Test Thesis Revision",
    statement: "Demand remains resilient.",
    rationale: "Recent evidence supports the revision.",
    confidence_band: "medium",
    rating: "watch",
    invalidation_check_note: "Reject if demand falls below the stated threshold.",
    thesis_role: "primary",
    thesis_status: "active",
    assumptions: [],
    invalidations: [],
  },
};

const observationResearchDraft = {
  source: {
    note_revision_id: SOURCE_REVISION_ID,
    note_id: "external_note_019ff000-0000-7000-8000-000000000006",
    note_version: 4,
    title: "AAPL Living Note",
    instrument_id: "equity:US:AAPL",
    observed_at: "2026-08-28T10:00:00Z",
    analysis_kind: "ESCALATED_REVIEW",
    analysis_id: "review_019ff000-0000-7000-8000-000000000007",
    provider: "opencode_go",
    model: "qwen3.8-max",
    created_at: "2026-08-28T10:01:00Z",
  },
  payload: {
    material_change_summary: "USER sees a range-top setup that still needs confirmation.",
    viewpoints: [
      {
        speaker_kind: "USER",
        speaker_label: "USER",
        summary: "USER thesis text: wait for a confirmed breakout.",
        direction: "SIDEWAYS",
        holding_horizon: "medium",
        structure: "Range remains intact.",
        source_block_ordinals: [1, 2],
      },
      {
        speaker_kind: "NAMED_PERSON",
        speaker_label: "External Analyst",
        summary: "Analyst says momentum may improve.",
        direction: "UPSIDE",
        holding_horizon: "short",
        structure: "Watch resistance.",
        source_block_ordinals: [3],
      },
    ],
    user_scenarios: [
      { scenario: "UPSIDE", action: "REVIEW", condition: "Close above resistance.", confirmation: "Confirm with a second close.", loss_boundary: "Return to range." },
      { scenario: "SIDEWAYS", action: "NO_ACTION", condition: "Remain in range.", confirmation: "Review next catalyst.", loss_boundary: "No loss boundary yet." },
      { scenario: "PULLBACK", action: "REVIEW", condition: "Hold support.", confirmation: "Check volume.", loss_boundary: "Support failure." },
      { scenario: "INVALIDATION", action: "REVIEW", condition: "Thesis breaks.", confirmation: "Reassess evidence.", loss_boundary: "Retire the Thesis." },
    ],
    catalysts: ["Next earnings call"],
    key_levels: ["USER reference level 180; verify speaker and date."],
    missing_evidence: ["Volume confirmation"],
    contradictions: ["Analyst view differs from USER view."],
  },
  warnings: [],
};

const importedPlanNotes = [
  `Observation revision: ${SOURCE_REVISION_ID} (v4)\nAnalysis: ESCALATED_REVIEW · qwen3.8-max · review_019ff000-0000-7000-8000-000000000007`,
  "USER thesis text: wait for a confirmed breakout.\nDirection: SIDEWAYS; horizon: medium.\nStructure: Range remains intact.\nSource blocks: 1, 2",
  "UPSIDE · REVIEW\nCondition: Close above resistance.\nConfirmation: Confirm with a second close.\nLoss boundary: Return to range.\n\nSIDEWAYS · NO_ACTION\nCondition: Remain in range.\nConfirmation: Review next catalyst.\nLoss boundary: No loss boundary yet.\n\nPULLBACK · REVIEW\nCondition: Hold support.\nConfirmation: Check volume.\nLoss boundary: Support failure.\n\nINVALIDATION · REVIEW\nCondition: Thesis breaks.\nConfirmation: Reassess evidence.\nLoss boundary: Retire the Thesis.",
  "Parsed change summary\nUSER sees a range-top setup that still needs confirmation.\n\nParsed level references (verify speaker, date, and role before use)\n- USER reference level 180; verify speaker and date.\n\nCatalyst references\n- Next earnings call\n\nMissing evidence\n- Volume confirmation\n\nContradictions to review\n- Analyst view differs from USER view.",
].join("\n\n");

function researchPayload(includeCandidate: boolean, created = false, existingPlan = false) {
  const subjectId = created ? CREATED_SUBJECT_ID : SUBJECT_ID;
  const resolvedSubjectId = existingPlan ? EXISTING_PLAN_SUBJECT_ID : subjectId;
  const subjectTitle = created ? "AAPL Observation Research" : existingPlan ? "AAPL Existing Plan" : "Candidate Review E2E";
  const subjectInstrument = created || existingPlan ? "equity:US:AAPL" : "equity:US:TEST";
  const existingPlanData = existingPlan ? {
    plan_id: "trade_plan_existing",
    version: 2,
    thesis_id: "thesis_existing",
    instrument_id: "equity:US:AAPL",
    status: "DRAFT",
    currency: "USD",
    reference_price: "100",
    reference_price_at: "2026-08-28T09:00:00Z",
    valid_from: "2026-08-28T09:00:00Z",
    valid_until: null,
    target_position_percent: "20",
    max_position_percent: "30",
    risk_budget_percent: "5",
    stop_price: "90",
    notes: importedPlanNotes,
    conditions: [{ condition_code: `obs_upside_${SOURCE_TOKEN}`, phase: "REVIEW", mode: "MANUAL", description: "Existing imported condition", severity: "MEDIUM" }],
  } : null;
  return {
    subjects: [{
      subject: {
        subject_id: resolvedSubjectId,
        subject_type: "company",
        title: subjectTitle,
        summary: "Verify explicit review behavior.",
        primary_instrument_id: subjectInstrument,
        status: "active",
        topic_tags: [],
        linked_subject_ids: [],
        created_at: "2026-08-20T09:00:00Z",
        updated_at: "2026-08-20T10:00:00Z",
      },
      state: {
        ok: true,
        data: {
          theses: existingPlan ? [{ thesis_id: "thesis_existing", title: "Existing Thesis", status: "ACTIVE", role: "PRIMARY" }] : [],
          latest_revisions: [],
          assumptions: [],
          invalidations: [],
          open_questions: [],
          watchlist_items: [],
          pending_candidates: includeCandidate && !created ? [candidate] : [],
          current_trade_plan: existingPlanData,
          trade_plan_versions: existingPlanData ? [existingPlanData] : [],
        },
        warnings: [],
        errors: [],
        degraded: false,
      },
    }],
    subject_list: { pages: [], total: 1, page_size: 200 },
  };
}

async function mockResearchApi(page: Page, rejectSucceeds: boolean, existingPlan = false) {
  let rejected = false;
  let created = false;
  const decisionBodies: unknown[] = [];
  await page.route("**/api/console/api/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    const json = (value: unknown, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(value),
    });
    if (path === "/api/session") return json({ token: "e2e-session-token-0000000000000000" });
    if (path === "/api/research") return json(researchPayload(!rejected, created, existingPlan));
    if (path === `/api/observations/${SOURCE_REVISION_ID}/research-draft`) return json({ data: observationResearchDraft });
    if (path.startsWith("/api/monitors")) return json({ dashboard: { ok: true, data: { items: [] } } });
    if (path === "/api/tools/invoke" && request.method() === "POST") {
      const body = request.postDataJSON();
      decisionBodies.push(body);
      if (!rejectSucceeds) {
        return json({
          result: {
            ok: false,
            errors: [{ code: "CANDIDATE_REJECT_FAILED", message: "Candidate could not be rejected." }],
          },
        });
      }
      const requestBody = (body as { arguments?: { request?: { operation?: string } } }).arguments?.request;
      if (requestBody?.operation === "create") {
        created = true;
        return json({ result: { ok: true, data: { case_id: CREATED_SUBJECT_ID } } });
      }
      rejected = true;
      return json({ result: { ok: true, data: { candidate: { ...candidate, status: "rejected" } } } });
    }
    return json({});
  });
  return { decisionBodies };
}

test("Reject Candidate submits the exact rationale and removes the reviewed Candidate", async ({ page }) => {
  const api = await mockResearchApi(page, true);
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  await expect(page.getByText("Test Thesis Revision", { exact: false }).first()).toBeVisible();

  const card = page.locator(".research-candidate").first();
  await card.getByLabel("Candidate Rejection Reason").fill("Evidence does not support this revision.");
  await card.getByRole("button", { name: "Reject Candidate" }).click();

  await expect(page.locator(".research-candidate")).toHaveCount(0);
  expect(api.decisionBodies).toEqual([{
    tool_name: "research_judgment_confirm",
    arguments: {
      request: {
        operation: "candidate",
        candidate_id: CANDIDATE_ID,
        action: "reject",
        reviewed_by: "user",
        submitted_via: "direct",
        rejection_reason: "Evidence does not support this revision.",
      },
    },
    confirmation: "research_judgment_confirm",
  }]);
});

test("Reject Candidate keeps a failed decision error on its own card", async ({ page }) => {
  await mockResearchApi(page, false);
  await page.goto(`/research#subject-${SUBJECT_ID}`);
  const card = page.locator(".research-candidate").first();
  await card.getByLabel("Candidate Rejection Reason").fill("The evidence is incomplete.");
  await card.getByRole("button", { name: "Reject Candidate" }).click();

  await expect(card.getByRole("alert")).toContainText("CANDIDATE_REJECT_FAILED");
  await expect(card).toBeVisible();
});

test("Observation without a Subject opens one prefilled Research draft", async ({ page }) => {
  await mockResearchApi(page, true);
  await page.goto("/research?create=observation&instrument_id=equity%3AUS%3AAFRM&title=AFRM");

  await expect(page.getByLabel("Title")).toHaveValue("AFRM Research");
  await expect(page.getByLabel("Primary Instrument ID")).toHaveValue("equity:US:AFRM");
  await expect(page.getByLabel("Summary")).toHaveValue(/evolving external observations/);
  await expect(page.getByRole("button", { name: "Create Research Subject" })).toBeVisible();
});

test("Observation Review keeps only the exact revision in the URL and previews safe source context", async ({ page }) => {
  await mockResearchApi(page, true);
  await page.goto(`/research?create=observation&note_revision_id=${SOURCE_REVISION_ID}`);

  const url = new URL(page.url());
  expect(url.searchParams.get("note_revision_id")).toBe(SOURCE_REVISION_ID);
  expect(page.url()).not.toContain("AAPL%20Living%20Note");
  expect(page.url()).not.toContain("qwen3.8-max");
  expect(page.url()).not.toContain("USER%20thesis");
  await expect(page.getByText("AAPL Living Note", { exact: true })).toBeVisible();
  await expect(page.getByText("USER thesis text: wait for a confirmed breakout.", { exact: true })).toBeVisible();
  await expect(page.getByText("USER reference level 180; verify speaker and date.", { exact: true })).toBeVisible();
  await expect(page.getByText("External Analyst", { exact: true })).toBeVisible();
  await expect(page.locator(".research-observation-source-meta").getByText(/qwen3\.8-max/)).toBeVisible();
});

test("Creating an Observation Research Subject opens a USER Thesis draft and preserves manual Plan seeds", async ({ page }) => {
  const api = await mockResearchApi(page, true);
  await page.goto(`/research?create=observation&note_revision_id=${SOURCE_REVISION_ID}`);
  await page.getByRole("button", { name: "Create Research Subject", exact: true }).click();

  const statement = page.getByLabel("Statement");
  await expect(statement).toHaveValue("USER thesis text: wait for a confirmed breakout.");
  await expect(statement).not.toHaveValue(/External Analyst/);
  await expect(page.getByText("USER reference level 180; verify speaker and date.", { exact: true })).toBeVisible();
  expect(api.decisionBodies).toHaveLength(1);
  expect((api.decisionBodies[0] as { arguments?: { request?: { operation?: string } } }).arguments?.request?.operation).toBe("create");

  await page.reload();
  const createdUrl = new URL(page.url());
  expect(createdUrl.hash).toBe(`#subject-${CREATED_SUBJECT_ID}`);
  expect(createdUrl.searchParams.get("create")).toBeNull();
  await expect(page.getByRole("button", { name: "Create Research Subject", exact: true })).toHaveCount(0);
  expect(api.decisionBodies).toHaveLength(1);
  expect(api.decisionBodies.some((body) => (body as { arguments?: { request?: { operation?: string } } }).arguments?.request?.operation === "thesis_revision")).toBe(false);

  await page.getByRole("button", { name: "Use Review in Plan Draft", exact: true }).click();
  await expect(page.getByLabel("Plan Notes")).toHaveValue(/USER thesis text: wait for a confirmed breakout/);
  await expect(page.getByLabel("Condition Meaning")).toHaveCount(4);
  await expect(page.getByLabel("Condition Meaning").first()).toHaveValue(/Close above resistance/);
  expect(api.decisionBodies.every((body) => (body as { arguments?: { request?: { operation?: string } } }).arguments?.request?.operation === "create")).toBe(true);
});

test("Observation Review carryover is disabled for a Research Subject with the wrong Instrument", async ({ page }) => {
  await mockResearchApi(page, true);
  await page.goto(`/research?note_revision_id=${SOURCE_REVISION_ID}#subject-${SUBJECT_ID}`);
  await expect(page.getByText("INSTRUMENT CHECK REQUIRED", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Use Review in Thesis", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Use Review in Plan Draft", exact: true })).toBeDisabled();
});

test("Existing Plan Review carryover preserves numeric fields and deduplicates notes and conditions", async ({ page }) => {
  const api = await mockResearchApi(page, true, true);
  await page.goto(`/research?note_revision_id=${SOURCE_REVISION_ID}#subject-${EXISTING_PLAN_SUBJECT_ID}`);
  await page.getByRole("button", { name: "Use Review in Plan Draft", exact: true }).click();

  await expect(page.getByRole("spinbutton", { name: "Reference Price", exact: true })).toHaveValue("100");
  await expect(page.getByLabel("Target Position %")).toHaveValue("20");
  await expect(page.getByLabel("Max Position %")).toHaveValue("30");
  await expect(page.getByLabel("Risk Budget %")).toHaveValue("5");
  await expect(page.getByLabel("Stop Price")).toHaveValue("90");
  await expect(page.getByLabel("Plan Notes")).toHaveValue(importedPlanNotes);
  await expect(page.getByLabel("Condition Code")).toHaveCount(4);
  await expect(page.getByLabel("Condition Code").first()).toHaveValue(`obs_upside_${SOURCE_TOKEN}`);
  expect(api.decisionBodies).toHaveLength(0);

  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Use Review in Plan Draft", exact: true }).click();
  await expect(page.getByLabel("Plan Notes")).toHaveValue(importedPlanNotes);
  await expect(page.getByLabel("Condition Code")).toHaveCount(4);
});
