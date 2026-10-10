import { expect, test, type Page } from "@playwright/test";

const SUBJECT_A = "case_manual_observation_a";
const SUBJECT_B = "case_manual_observation_b";
const REVISION = "external_note_revision_manual_full";
const NOTE = "external_note_manual";
const INSTRUMENT = "equity:US:AAPL";

function envelope(data: Record<string, unknown>) {
  return { ok: true, data, warnings: [], errors: [], degraded: false };
}

function workbench(includeSecondSubject = true) {
  const subject = (subjectId: string, title: string) => ({
    subject: {
      subject_id: subjectId,
      subject_type: "company",
      title,
      summary: "Synthetic Subject",
      primary_instrument_id: INSTRUMENT,
      status: "ACTIVE",
    },
    state: envelope({
      theses: [],
      latest_revisions: [],
      pending_candidates: [],
      open_questions: [],
      current_trade_plan: null,
    }),
  });
  return {
    selected_subject_id: SUBJECT_A,
    subjects: [subject(SUBJECT_A, "Apple Long Horizon"), ...(includeSecondSubject ? [subject(SUBJECT_B, "Apple Tactical Scope")] : [])],
    accounts: envelope({ accounts: [] }),
    transactions: envelope({ transactions: [] }),
    trade_cycles: envelope({ status: "COMPLETE", cycles: [] }),
    behavior: envelope({}),
    performance_series: envelope({ series: [] }),
    daily_equity: envelope({ items: [] }),
    monitors: envelope({ items: [] }),
    agenda: envelope({ items: [] }),
    retro: envelope({ runs: [] }),
    scorecards: envelope({ runs: [] }),
    timeline: envelope({ items: [] }),
    review_items: [],
    review_item_metrics: { open_count: 0 },
    partial_failures: [],
    external_notes: [],
    order_intents: [],
    behavior_review_runs: [],
    activity_annotations: [],
  };
}

function note(mode: "failed-full" | "summary-only") {
  return {
    identity: { note_id: NOTE, title: "Apple Manual Source", primary_instrument_id: INSTRUMENT, source: "Moomoo" },
    revision: {
      note_revision_id: REVISION,
      version: 4,
      coverage: mode === "summary-only" ? "SUMMARY_ONLY" : "FULL",
      observed_at: "2026-09-20T08:00:00Z",
      summary: "Manual source summary remains available.",
      blocks: mode === "summary-only" ? [] : [{ ordinal: 0, speaker_label: "USER", body: "Review the operating evidence." }],
    },
    interpretation: { status: mode === "summary-only" ? "PENDING" : "FAILED", error_code: "SYNTHETIC_ANALYSIS_FAILED", payload: {} },
    review: { status: "PENDING", review_id: "review_manual", version: 2 },
  };
}

async function mockJournal(page: Page, mode: "failed-full" | "summary-only" = "failed-full", includeSecondSubject = false) {
  const writes: Array<{ path: string; body: Record<string, unknown> }> = [];
  await page.route("**/api/{console,design-preview}/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/(?:console|design-preview)/, "");
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (request.method() !== "GET") {
      const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;
      writes.push({ path, body });
      if (path === "/api/tools/invoke") return json({ result: { ok: true, data: { decision_id: "decision_manual" } } });
      return json({ detail: "Unexpected Observation write" }, 403);
    }
    if (path === "/api/session") return json({ token: "manual-observation-session-token-000000" });
    if (path.startsWith("/api/decision-workbench")) return json(workbench(includeSecondSubject));
    if (path === "/api/observations") return json({ data: { external_notes: [note(mode)], observation_sources: [], review_workflow_enabled: true } });
    if (path === "/api/current-view") return json({ data: {} });
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
    if (path.startsWith("/api/monitors")) return json({ dashboard: { ok: true, data: { items: [] } } });
    if (path === "/account-aliases") return json({ aliases: {} });
    return json({ items: [] });
  });
  return writes;
}

test("FULL failed interpretation opens manual review without prepare/model/adoption writes", async ({ page }) => {
  const writes = await mockJournal(page);
  await page.goto(`/decision-workbench?subject_id=${SUBJECT_A}#notes`);
  const manual = page.getByRole("button", { name: "Record Independent Decision", exact: true });
  await expect(manual).toBeVisible();
  await manual.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Manual Source Review");
  await expect(dialog).toContainText("Observation remains in its current review status");
  await expect(dialog.getByLabel("Action")).toHaveValue("no_action");
  await expect(dialog.getByLabel("Reason")).toHaveValue("");
  expect(writes).toEqual([]);

  await dialog.getByLabel("Reason").fill("I reviewed the exact FULL source independently.");
  await dialog.getByRole("button", { name: "Confirm & Record Independent Decision", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].path).toBe("/api/tools/invoke");
  expect(writes[0].body.confirmation).toBe("research_memory_append");
  const request = (writes[0].body.arguments as { request?: Record<string, unknown> }).request;
  expect(request?.external_note_revision_id).toBe(REVISION);
  expect(request?.decision_type).toBe("no_action");
  expect(request?.rationale).toContain("Manual Source Review");
});

test("SUMMARY_ONLY source cannot open Manual Source Review", async ({ page }) => {
  await mockJournal(page, "summary-only");
  await page.goto(`/decision-workbench?subject_id=${SUBJECT_A}#notes`);
  await expect(page.getByRole("button", { name: "Record Independent Decision", exact: true })).toHaveCount(0);
  await expect(page.getByText(/SUMMARY_ONLY: full source text is unavailable/)).toBeVisible();
});

test("multiple active Subjects require explicit selection before manual review", async ({ page }) => {
  await mockJournal(page, "failed-full", true);
  await page.goto(`/decision-workbench?subject_id=${SUBJECT_A}#notes`);
  await expect(page.getByRole("button", { name: "Record Independent Decision", exact: true })).toHaveCount(0);
  const selector = page.getByLabel("Research Subject for This Note", { exact: true });
  await expect(selector).toBeVisible();
  await selector.selectOption(SUBJECT_B);
  await expect(page.getByRole("button", { name: "Record Independent Decision", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Record Independent Decision", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Apple Tactical Scope");
});
