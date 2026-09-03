import { expect, test, type Page, type Route } from "@playwright/test";

const SUBJECT_ID = "case_019ff000-0000-7000-8000-000000000001";
const CANDIDATE_ID = "run_019ff000-0000-7000-8000-000000000002";

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

function researchPayload(includeCandidate: boolean) {
  return {
    subjects: [{
      subject: {
        subject_id: SUBJECT_ID,
        subject_type: "company",
        title: "Candidate Review E2E",
        summary: "Verify explicit review behavior.",
        primary_instrument_id: "equity:US:TEST",
        status: "active",
        topic_tags: [],
        linked_subject_ids: [],
        created_at: "2026-08-20T09:00:00Z",
        updated_at: "2026-08-20T10:00:00Z",
      },
      state: {
        ok: true,
        data: {
          theses: [],
          latest_revisions: [],
          assumptions: [],
          invalidations: [],
          open_questions: [],
          watchlist_items: [],
          pending_candidates: includeCandidate ? [candidate] : [],
          current_trade_plan: null,
          trade_plan_versions: [],
        },
        warnings: [],
        errors: [],
        degraded: false,
      },
    }],
    subject_list: { pages: [], total: 1, page_size: 200 },
  };
}

async function mockResearchApi(page: Page, rejectSucceeds: boolean) {
  let rejected = false;
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
    if (path === "/api/research") return json(researchPayload(!rejected));
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
