import { expect, test, type Page } from "@playwright/test";

const REVISION = "external_note_revision_synthetic_2";
const envelope = (data: object) => ({ ok: true, data, warnings: [], errors: [] });
const note = {
  identity: { note_id: "synthetic-note", title: "合成观察笔记", primary_instrument_id: "equity:US:AAPL" },
  revision: { note_revision_id: REVISION, version: 2, coverage: "FULL", observed_at: "2026-09-01T12:00:00Z", summary: "合成数据：等待证据。", blocks: [{ ordinal: 0, speaker_label: "USER", body: "合成数据：等待证据。" }] },
  interpretation: { status: "SUCCEEDED", payload: { material_change_summary: "等待证据。", viewpoints: [], user_scenarios: [] } },
  review: { status: "PENDING", review_id: "synthetic-review", version: 1 },
};

async function setup(page: Page) {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const reads: string[] = [];
  let status = "RUNNING";
  let requestId = "synthetic-run";
  const run = () => ({ status, attempt: 1, result_code: status === "SUCCEEDED" ? "OBSERVATION_REFRESH_DEGRADED" : null, error_code: null });
  const result = () => ({ data: { request_id: requestId, run: run(), stages: { CAPTURE: { ...run(), status: "SUCCEEDED" }, INTERPRET: { ...run(), status: "SUCCEEDED" }, REVIEW: run() } } });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api\/(?:console|design-preview)/, "");
    const json = (value: unknown) => route.fulfill({ json: value });
    if (request.method() === "POST") {
      const body = request.postDataJSON();
      writes.push({ path, body });
      if (path === "/api/observation-refresh") { requestId = body.request_id; return json(result()); }
      return route.fulfill({ status: 403, json: { detail: "Unexpected synthetic write" } });
    }
    reads.push(path);
    if (path === "/api/session") return json({ token: "synthetic-session-token-00000000000000" });
    if (path.startsWith("/api/observation-refresh/")) return json(result());
    if (path === `/api/observations/${REVISION}/review`) return json({ data: {
      note_revision_id: REVISION, note_version: 2, observed_at: note.revision.observed_at,
      subject_id: "synthetic-subject", thesis: { revision_id: "synthetic-thesis-revision-7", title: "Current thesis", statement: "Current confirmed statement" },
      trade_plan: { plan_id: "synthetic-plan", version: 3, status: "ACTIVE" },
      latest_decision: { decision_id: "synthetic-decision", title: "Wait for evidence", rationale: "Synthetic current rationale", decided_at: "2026-09-02T12:00:00Z" },
      review: { status: "PENDING" }, coverage: { accounts: "INCOMPLETE" }, deep_review: { status: "FAILED", error_code: "NOTE_REVIEW_UNGROUNDED_TIME_CONDITION" },
    } });
    if (path === "/api/observations") return json({ data: { external_notes: [note], observation_sources: [] } });
    if (path === "/api/decision-workbench") return json({ selected_subject_id: null, subjects: [], partial_failures: [], accounts: envelope({ accounts: [] }), transactions: envelope({ transactions: [], history_complete: true }), trade_cycles: envelope({ cycles: [], status: "INCOMPLETE" }), external_notes: [note] });
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
    return json({ items: [], aliases: {} });
  });
  return { writes, reads, complete: () => { status = "SUCCEEDED"; } };
}

test("Observation refresh survives reload without replaying its write", async ({ page }) => {
  const mock = await setup(page);
  await page.goto("/decision-workbench#notes");
  await page.getByRole("button", { name: "Refresh Sources", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Observation Refresh", exact: true })).toBeVisible();
  await expect.poll(() => mock.writes.length).toBe(1);
  const requestId = mock.writes[0].body.request_id;
  await page.reload();
  await expect(page.getByRole("heading", { name: "Observation Refresh", exact: true })).toBeVisible();
  await expect.poll(() => mock.reads.filter((path) => path === `/api/observation-refresh/${requestId}`).length).toBeGreaterThan(1);
  expect(mock.writes).toHaveLength(1);
  mock.complete();
  await expect(page.getByText("Some data or drafts need attention.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Resume Failed Stages" })).toHaveCount(0);
  expect(mock.writes[0].path).toBe("/api/observation-refresh");
});

test("Observation context exposes exact revisions using reads only", async ({ page }) => {
  const mock = await setup(page);
  await page.goto("/decision-workbench#notes");
  await page.getByRole("button", { name: "Load Review Context", exact: true }).click();
  await expect(page.getByText(REVISION, { exact: true })).toBeVisible();
  await expect(page.getByText("synthetic-thesis-revision-7", { exact: true })).toBeVisible();
  await expect(page.getByText("synthetic-plan · v3", { exact: true })).toBeVisible();
  await expect(page.getByText(/synthetic-decision ·/)).toBeVisible();
  await expect(page.getByText("NOTE_REVIEW_UNGROUNDED_TIME_CONDITION", { exact: true })).toBeVisible();
  await expect(page.getByText(/not a reconstruction of what was known/)).toBeVisible();
  await expect(page.getByRole("region", { name: "Observation Review Context" }).getByRole("link", { name: "Open Research", exact: true })).toHaveAttribute("href", "/research#subject-synthetic-subject");
  expect(mock.reads).toContain(`/api/observations/${REVISION}/review`);
  expect(mock.writes).toEqual([]);
});
