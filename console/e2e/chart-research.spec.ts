import { expect, test, type Page } from "@playwright/test";
import { syntheticTechnicalChartScene } from "../dev/chart-fixtures";

const SUBJECT = "case_019ff000-0000-7000-8000-000000000031";
const INSTRUMENT = "equity:US:TTWO";
const NOTE = "Synthetic interpretation: investigate the structure alongside business evidence.";

function scene() {
  const result = syntheticTechnicalChartScene();
  return { ...result, as_of: result.bars.at(-1)!.timestamp, algorithm_version: "tp_technical_v3", timeframes: result.timeframes.map((frame) => ({ ...frame, smart_money: { ...frame.smart_money, algorithm_version: "tp_smc_v1", liquidity_levels: [{ kind: "equal_high", scope: "swing", price: "130", first_swing_at: result.bars[195].timestamp, second_swing_at: result.bars[205].timestamp, confirmed_at: result.bars[230].timestamp, status: "swept" }] } })) };
}

async function mock(page: Page, delayed = false) {
  const chartReads: unknown[] = [];
  const writes: string[] = [];
  let release: () => void = () => {};
  const held = delayed ? new Promise<void>((resolve) => { release = resolve; }) : null;
  await page.route("**/api/console/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    if (path === "/api/session") return route.fulfill({ json: { token: "synthetic-chart-review-token-00000000000000000" } });
    if (path === "/api/tools/invoke" && request.method() === "POST") {
      const payload = request.postDataJSON();
      if (payload.tool_name !== "technical_get_snapshot") { writes.push(payload.tool_name); return route.fulfill({ status: 403, json: { detail: "Unexpected synthetic write" } }); }
      chartReads.push(payload);
      expect(payload.preserve_full_result).toBe(true);
      expect(payload.arguments.include_bars).toBe(true);
      if (held) await held;
      await route.fulfill({ json: { result: { ok: true, data: scene(), warnings: [], errors: [] } } }).catch(() => {});
      return;
    }
    if (request.method() !== "GET") { writes.push(path); return route.fulfill({ status: 403, json: { detail: "Unexpected synthetic write" } }); }
    if (path === "/api/agent/status") return route.fulfill({ json: { enabled: false, configured: false, available: false, state: "DISABLED", providers: [], models: [], components: {} } });
    if (path === "/api/research") return route.fulfill({ json: { subjects: [{ subject: { subject_id: SUBJECT, title: "Synthetic Chart Research", subject_type: "company", summary: "Synthetic scope", status: "active", primary_instrument_id: INSTRUMENT, topic_tags: [], linked_subject_ids: [], created_at: scene().as_of, updated_at: scene().as_of }, state: { ok: true, data: { theses: [], latest_revisions: [], assumptions: [], invalidations: [], open_questions: [], watchlist_items: [], pending_candidates: [], trade_plan_versions: [], current_trade_plan: null } } }] } });
    if (path.endsWith("/changes")) return route.fulfill({ json: { data: { subject_id: SUBJECT, as_of: scene().as_of, baseline: null, coverage: { observations: "COMPLETE", monitors: "COMPLETE", agenda: "COMPLETE" }, warning_codes: [], items: [], total: 0, offset: 0, limit: 25, has_more: false } } });
    if (path.startsWith("/api/monitors")) return route.fulfill({ json: { dashboard: { ok: true, data: { items: [] } } } });
    return route.fulfill({ json: { items: [] } });
  });
  return { chartReads, writes, release };
}

async function open(page: Page) {
  await page.goto(`/charts?instrument_id=${encodeURIComponent(INSTRUMENT)}&subject_id=${SUBJECT}`);
  await expect(page.getByRole("heading", { name: "Charts", exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: /Instrument ID/ })).toHaveValue(INSTRUMENT);
  const close = page.getByRole("button", { name: "Close Copilot Panel", exact: true });
  if (await close.isVisible()) await close.click();
}

test("chart confirmation cutoff and explicit handoff preserve source metadata without creating judgment", async ({ page }) => {
  const api = await mock(page);
  await open(page);
  await expect(page.getByText("No matching ACTIVE Plan is recorded.")).toBeVisible();
  expect(api.chartReads).toHaveLength(0);
  await page.getByRole("button", { name: "Open Chart", exact: true }).click();
  await expect(page.getByRole("img", { name: /interactive candlestick chart/ })).toBeVisible();
  const inspector = page.getByRole("combobox", { name: /Inspect Structure/ });
  await expect(inspector.getByRole("option", { name: /equal_high/ })).toHaveCount(1);
  const cutoff = scene().bars[220].timestamp.slice(0, 16);
  await page.getByLabel("Historical Cutoff (UTC)").fill(cutoff);
  await expect(inspector.getByRole("option", { name: /equal_high/ })).toHaveCount(0);
  await inspector.selectOption("event-0");
  await expect(page.getByText(`Occurred ${scene().bars[210].timestamp} · Confirmed ${scene().bars[210].timestamp}`)).toBeVisible();
  await expect(page.getByText(/Algorithm tp_technical_v3 · SMC tp_smc_v1/)).toBeVisible();
  await expect(page.getByText(/historical invalidation, mitigation and sweep status cannot be reconstructed/)).toBeVisible();
  await page.getByRole("button", { name: "Use as Research Context" }).click();
  await page.getByLabel("Your Interpretation").fill(NOTE);
  await page.getByRole("button", { name: "Continue in Research" }).click();
  await expect(page).toHaveURL(/\/research\?.*chart_context=/);
  expect(decodeURIComponent(page.url())).not.toContain(NOTE);
  await expect(page.getByRole("heading", { name: "Review Chart Context" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Create Thesis", exact: true })).toHaveCount(0);
  expect(api.writes).toEqual([]);
  await page.getByRole("button", { name: "Accept Context into Draft" }).click();
  const editor = page.getByRole("region", { name: "Create Thesis", exact: true });
  await expect(editor).toBeVisible();
  await expect(editor.getByLabel("Candidate Status")).toHaveValue("draft");
  await expect(editor.getByRole("textbox", { name: /Statement/ })).toHaveValue(NOTE);
  const rationale = await editor.getByRole("textbox", { name: /Rationale/ }).inputValue();
  for (const value of [INSTRUMENT, SUBJECT, "tp_technical_v3", "tp_smc_v1", scene().price_basis, "Source interval: 1d", cutoff, "Status at snapshot: confirmed"]) expect(rationale).toContain(value);
  expect(new URL(page.url()).searchParams.has("chart_context")).toBe(false);
  expect(api.chartReads).toHaveLength(1);
  expect(api.writes).toEqual([]);
});

test("instrument selector change ignores an in-flight chart result", async ({ page }) => {
  const api = await mock(page, true);
  await open(page);
  await page.getByRole("button", { name: "Open Chart", exact: true }).click();
  await expect.poll(() => api.chartReads.length).toBe(1);
  await page.getByRole("textbox", { name: /Instrument ID/ }).fill("equity:US:OTHER");
  api.release();
  await expect(page.getByRole("button", { name: "Open Chart", exact: true })).toBeEnabled();
  await expect(page.getByRole("img", { name: /interactive candlestick chart/ })).toHaveCount(0);
  await expect(page.getByText(/Use as Research Context/)).toHaveCount(0);
  expect(api.writes).toEqual([]);
});
