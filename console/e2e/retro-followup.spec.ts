import { expect, test, type Page } from "@playwright/test";
import { syntheticResearchWorkspace, syntheticResearchLinks, syntheticResearchChanges, RESEARCH_SAMPLE } from "../dev/research-workflow-fixtures";
const RUN = "retro_synthetic";
const FINDING = "finding_" + "a".repeat(64);
const AT = "2026-10-04T00:00:00Z";
const workspace = syntheticResearchWorkspace();
const subject = workspace.subjects[0].subject;
const question = workspace.subjects[0].state.data.open_questions[0];
const finding = { finding_key: FINDING, code: "NO_PRETRADE_PLAN", title: "Verify the missing plan", detail: "Original audit has no pre-period snapshot", severity: "HIGH", instrument_id: subject.primary_instrument_id };
const workflow = { run_id: RUN, as_of: AT, original_run_status: "INCOMPLETE", metrics: { recorded_trade_references: 2, new_plan_versions_during_period: 1 }, warning_codes: ["NO_ELIGIBLE_PREPERIOD_SNAPSHOT"], findings: [{ ...finding, subjects: [{ subject_id: RESEARCH_SAMPLE, title: subject.title, questions_complete: true, questions: [{ ...question, version: 1 }] }] }] };
async function mock(page: Page, progress?: Record<string, unknown>, loss?: "committed" | "not_committed" | "receipt_unavailable" | "conflict") {
  const writes: Record<string, unknown>[] = [];
  let latestReview: Record<string, unknown> | null = null;
  let receiptReads = 0;
  await page.route("**/api/console/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/console", "");
    const json = (data: unknown) => route.fulfill({ json: data });
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON(); writes.push(body);
      const request = body.arguments?.request;
      if (loss === "conflict" && request?.action === "review" && writes.length === 1) {
        latestReview = { run_id: RUN, version: 1, status: "DISPUTED", note_markdown: "Concurrent saved note", action_items: ["Different saved action"], finding_reviews: [] };
        return json({ result: { ok: false, errors: [{ code: "TRADE_RETRO_REVIEW_VERSION_CONFLICT", message: "Review version changed" }] } });
      }
      if (request?.action === "review" && !(loss === "not_committed" && writes.length === 1)) latestReview = { run_id: RUN, version: Number(request.expected_version) + 1, note_markdown: request.note_markdown, action_items: request.action_items, status: request.review_status, finding_reviews: request.finding_reviews, idempotency_key: request.idempotency_key };
      if (loss && loss !== "conflict" && writes.length === 1) return route.abort("failed");
      return json({ result: { ok: true, data: { candidate_id: "candidate_synthetic", ...latestReview }, errors: [] } });
    }
    if (path.endsWith("/review-receipt")) {
      receiptReads++;
      if (loss === "receipt_unavailable" && receiptReads === 1) return route.fulfill({ status: 409, json: { detail: "Synthetic receipt unavailable" } });
      return json({ data: latestReview });
    }
    if (path === "/api/retro/retro_later/detail") return json({ data: { run_id: "retro_later", period_start: AT, period_end: "2026-10-08T00:00:00Z", generated_at: "2026-10-09T00:00:00Z", status: "INCOMPLETE", plan_snapshot_id: "retro_plan_later", findings: [finding], latest_review: { status: "DISPUTED", note_markdown: "Later review rationale" } } });
    if (path === "/api/session") return json({ token: "synthetic-retro-workflow-session-token" });
    if (path === "/api/research") return json(workspace);
    if (path.endsWith("/evidence-links")) return json({ data: syntheticResearchLinks() });
    if (path.endsWith("/changes")) return json({ data: syntheticResearchChanges() });
    if (path === `/api/retro/${RUN}/workflow`) return json({ data: { ...workflow, followup_progress: progress } });
    if (path === "/api/decision-workbench") return json({ subjects: workspace.subjects, subject_list: { total: workspace.subjects.length, ok: true }, retro: { ok: true, data: { runs: [{ run_id: RUN, subject_ids: [RESEARCH_SAMPLE], status: "INCOMPLETE", period_start: "2026-09-27T00:00:00Z", period_end: AT, generated_at: AT, findings: [finding], latest_review: latestReview }] } }, partial_failures: [], review_items: [] });
    if (path === "/api/retro") return json({});
    return json({ items: [] });
  });
  return writes;
}

test("Retro coverage stays unknown and linking a question needs an explicit review save", async ({ page }) => {
  const writes = await mock(page);
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Review Findings", exact: true }).click();
  const coverage = page.getByRole("region", { name: "Retro Coverage Basis" });
  await coverage.getByText("Coverage and Timing", { exact: true }).click();
  await expect(coverage.getByRole("row", { name: "Trades with one active pre-period Plan match Unknown" })).toBeVisible();
  await page.getByLabel("NO_PRETRADE_PLAN disposition").selectOption("ACCEPTED");
  await page.getByLabel("Follow-Up Choice").selectOption("QUESTION");
  await page.getByLabel("Confirmed Follow-Up Question", { exact: true }).selectOption(`${question.question_id}|1`);
  expect(writes).toEqual([]);
  await page.getByRole("button", { name: "Accept All", exact: true }).click();
  await expect(page.getByLabel("Confirmed Follow-Up Question", { exact: true })).toHaveValue(`${question.question_id}|1`);
  await page.getByRole("button", { name: "Append Review Revision" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ tool_name: "research_workflow_run", arguments: { request: { action: "review", expected_version: 0, finding_reviews: [{ finding_key: FINDING, followup: "QUESTION", question_id: question.question_id, question_version: 1 }] } } });
  await page.reload();
  await page.getByText("Saved Follow-up Choices", { exact: true }).click();
  await expect(page.getByText(`${FINDING} · QUESTION`, { exact: false })).toBeVisible();
  expect(writes).toHaveLength(1);
});

test("no-follow-up requires rationale and source handoff only opens an editable question draft", async ({ page }) => {
  const writes = await mock(page);
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Review Findings", exact: true }).click();
  await page.getByLabel("NO_PRETRADE_PLAN disposition").selectOption("ACCEPTED");
  await page.getByLabel("Follow-Up Choice").selectOption("NO_FOLLOWUP");
  await page.getByRole("button", { name: "Append Review Revision" }).click();
  await expect(page.getByText("Choose a finding disposition and complete its follow-up reference or rationale.")).toBeVisible();
  expect(writes).toEqual([]);
  await page.getByLabel("Subject for New Question").selectOption(RESEARCH_SAMPLE);
  await page.getByRole("link", { name: "Prepare Question from Finding" }).click();
  await page.getByRole("button", { name: "Use Retro Finding in Question Draft" }).click();
  await expect(page.getByRole("region", { name: "create research question" }).getByLabel("Question", { exact: false }).first()).toBeVisible();
  await expect(page.getByLabel("Progress Note")).toHaveValue(new RegExp(RUN));
  expect(writes).toEqual([]);
});


test("follow-up next step explains changed versions without claiming completion or writing", async ({ page }) => {
  const writes = await mock(page, { coverage: "PARTIAL", selected_count: 1, known_selected_count: 1,
    due_count: null, resolved_due_count: null, known_due_count: 0, unknown_deadline_count: 1,
    rows: [{ finding_key: FINDING, title: "Verify the missing plan", state: "QUESTION_CHANGED",
      question_id: question.question_id, subject_id: RESEARCH_SAMPLE, question_version: 1,
      due_at: null, later_similar_runs: [] }], warning_codes: [] });
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Inspect Coverage and Reload Follow-ups" }).click();
  const next = page.getByRole("region", { name: "Follow-Up Next Steps" });
  await expect(next.getByText("Question changed; review its new version", { exact: true })).toBeVisible();
  await expect(next).toContainText("Due count is incomplete");
  await expect(page.getByRole("row", { name: "Trades with one active pre-period Plan match Unknown" })).not.toBeVisible();
  await next.getByRole("link", { name: "Open Question and Evidence" }).click();
  await expect(page).toHaveURL(new RegExp(`question_id=${question.question_id}`));
  expect(writes).toEqual([]);
});


test("lost save response recovers the exact durable receipt without another POST", async ({ page }) => {
  const writes = await mock(page, undefined, "committed");
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Review Findings", exact: true }).click();
  await page.getByLabel("Review Note").fill("Synthetic recovery rationale");
  await page.getByRole("button", { name: "Append Review Revision" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Recovered the original receipt" })).toBeVisible();
  expect(writes).toHaveLength(1);
  await expect(page.getByRole("button", { name: "Revise Review", exact: true })).toBeVisible();
});

test("unconfirmed save retries only the frozen original request after explicit selection", async ({ page }) => {
  const writes = await mock(page, undefined, "not_committed");
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Review Findings", exact: true }).click();
  await page.getByLabel("Review Note").fill("Do not change this submitted draft");
  await page.getByRole("button", { name: "Append Review Revision" }).click();
  await expect(page.getByText("Save not confirmed yet. Check again or retry the original review with its same request key.")).toBeVisible();
  expect(writes).toHaveLength(1);
  await page.getByRole("button", { name: "Check Saved Review" }).click();
  await expect(page.getByRole("button", { name: "Retry Original Review" })).toBeEnabled();
  expect(writes).toHaveLength(1);
  await page.getByRole("button", { name: "Retry Original Review" }).click();
  await expect(page.getByRole("button", { name: "Revise Review", exact: true })).toBeVisible();
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
});


test("version conflict preserves edits and requires explicit comparison before a new save", async ({ page }) => {
  const writes = await mock(page, undefined, "conflict");
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Review Findings", exact: true }).click();
  await page.getByLabel("Review Note").fill("My unsaved rationale");
  await page.getByLabel("Action Items").fill("My planned follow-up");
  await page.getByRole("button", { name: "Append Review Revision" }).click();
  const conflict = page.getByRole("region", { name: "Review Version Conflict" });
  await expect(conflict).toContainText("Concurrent saved note");
  await expect(page.getByLabel("Review Note")).toHaveValue("My unsaved rationale");
  await expect(page.getByRole("button", { name: "Append Review Revision" })).toBeDisabled();
  await page.reload();
  await expect(page.getByLabel("Action Items")).toHaveValue("My planned follow-up");
  await page.getByRole("button", { name: "Keep My Draft Against v1" }).click();
  expect(writes).toHaveLength(1);
  await page.getByRole("button", { name: "Append Review Revision" }).click();
  await expect(page.getByRole("button", { name: "Revise Review", exact: true })).toBeVisible();
  expect(writes).toHaveLength(2);
  expect(writes[1]).toMatchObject({ arguments: { request: { expected_version: 1, note_markdown: "My unsaved rationale" } } });
});

test("navigation and partial question proposal preserve the unsubmitted Retro draft", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const writes = await mock(page);
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Review Findings", exact: true }).click();
  await page.getByLabel("Review Note").fill("Retain while I investigate");
  await page.getByLabel("Subject for New Question").selectOption(RESEARCH_SAMPLE);
  await page.getByRole("link", { name: "Prepare Question from Finding" }).click();
  await page.getByRole("button", { name: "Use Retro Finding in Question Draft" }).click();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.getByRole("region", { name: "create research question" }).getByRole("button", { name: "Propose Candidate" }).click();
  await expect(page.getByText("Candidate proposal created. Review it in Pending Candidates before confirmation.")).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ tool_name: "research_judgment_propose" });
  await page.goBack();
  await expect(page.getByLabel("Review Note")).toHaveValue("Retain while I investigate");
  await page.reload();
  await expect(page.getByLabel("Review Note")).toHaveValue("Retain while I investigate");
  expect(writes).toHaveLength(1);
});

test("reload of an unresolved submission reads the receipt and never replays the POST", async ({ page }) => {
  const writes = await mock(page, undefined, "receipt_unavailable");
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Review Findings", exact: true }).click();
  await page.getByLabel("Review Note").fill("Persist my exact submitted request");
  await page.getByRole("button", { name: "Append Review Revision" }).click();
  await expect(page.getByText(/Receipt read unavailable/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("status").filter({ hasText: "Recovered the original receipt" })).toBeVisible();
  expect(writes).toHaveLength(1);
});

for (const theme of ["light", "dark"]) test(`populated review is usable by keyboard at narrow width in ${theme}`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(value => localStorage.setItem("trading-partner-theme", value), theme);
  const writes = await mock(page);
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  const open = page.getByRole("button", { name: "Review Findings", exact: true });
  await open.focus(); await page.keyboard.press("Enter");
  await expect(page.getByLabel("Review Note")).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const note = page.getByLabel("Review Note");
  await note.focus(); await page.keyboard.type("Keyboard draft");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Action Items")).toBeFocused();
  await page.getByLabel("Follow-Up Choice").selectOption("QUESTION");
  const questionSelect = page.getByLabel("Confirmed Follow-Up Question", { exact: true });
  await questionSelect.scrollIntoViewIfNeeded();
  await questionSelect.focus();
  await expect(questionSelect).toBeFocused();
  const bounds = await questionSelect.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await expect.poll(() => questionSelect.evaluate(node => {
    const r = node.getBoundingClientRect();
    return node.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
  })).toBe(true);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Subject for New Question")).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: test.info().outputPath(`retro-${theme}-390.png`) });
  expect(writes).toEqual([]);
});


test("later reviews open in place and preserve the current draft without writes", async ({ page }) => {
  const writes = await mock(page, { coverage: "COMPLETE", due_count: 1, resolved_due_count: 0,
    rows: [{ finding_key: FINDING, title: "Current finding", finding_code: finding.code, instrument_id: finding.instrument_id, state: "DUE", later_similar_runs: ["retro_later"] }], warning_codes: [] });
  await page.goto(`/decision-workbench?subject_id=${RESEARCH_SAMPLE}#reviews`);
  await page.getByRole("button", { name: "Review Findings", exact: true }).click();
  await page.getByLabel("Review Note").fill("Keep my current interpretation");
  const entry = page.getByText("Inspect Historical Review · retro_later", { exact: true });
  await entry.click();
  await expect(page.getByRole("region", { name: "Historical Review Comparison" })).toContainText("Later review rationale");
  await entry.click();
  await expect(page.getByLabel("Review Note")).toHaveValue("Keep my current interpretation");
  expect(writes).toEqual([]);
  await expect(page).toHaveURL(new RegExp(`#reviews$`));
});
