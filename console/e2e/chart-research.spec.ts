import { expect, test, type Page } from "@playwright/test";
import { syntheticTechnicalChartScene } from "../dev/chart-fixtures";

const SUBJECT = "case_019ff000-0000-7000-8000-000000000031";
const INSTRUMENT = "equity:US:TTWO";
const NOTE = "Synthetic interpretation: investigate the structure alongside business evidence.";

function scene() {
  const result = syntheticTechnicalChartScene();
  return { ...result, as_of: result.bars.at(-1)!.timestamp, algorithm_version: "tp_technical_v3", timeframes: result.timeframes.map((frame) => ({ ...frame, smart_money: { ...frame.smart_money, algorithm_version: "tp_smc_v1", liquidity_levels: [{ kind: "equal_high", scope: "swing", price: "130", first_swing_at: result.bars[195].timestamp, second_swing_at: result.bars[205].timestamp, confirmed_at: result.bars[230].timestamp, status: "swept" }] } })) };
}

async function mock(page: Page, delayed = false, override?: { data: unknown; warnings: Array<{ code: string; message: string }>; instrument?: string }) {
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
      await route.fulfill({ json: { result: { ok: true, data: override?.data ?? scene(), warnings: override?.warnings ?? [], errors: [] } } }).catch(() => {});
      return;
    }
    if (request.method() !== "GET") { writes.push(path); return route.fulfill({ status: 403, json: { detail: "Unexpected synthetic write" } }); }
    if (path === "/api/agent/status") return route.fulfill({ json: { enabled: false, configured: false, available: false, state: "DISABLED", providers: [], models: [], components: {} } });
    if (path === "/api/research") return route.fulfill({ json: { subjects: [{ subject: { subject_id: SUBJECT, title: "Synthetic Chart Research", subject_type: "company", summary: "Synthetic scope", status: "active", primary_instrument_id: override?.instrument ?? INSTRUMENT, topic_tags: [], linked_subject_ids: [], created_at: scene().as_of, updated_at: scene().as_of }, state: { ok: true, data: { theses: [], latest_revisions: [], assumptions: [], invalidations: [], open_questions: [], watchlist_items: [], pending_candidates: [], trade_plan_versions: [], current_trade_plan: null } } }] } });
    if (path.endsWith("/changes")) return route.fulfill({ json: { data: { subject_id: SUBJECT, as_of: scene().as_of, baseline: null, coverage: { observations: "COMPLETE", monitors: "COMPLETE", agenda: "COMPLETE" }, warning_codes: [], items: [], total: 0, offset: 0, limit: 25, has_more: false } } });
    if (path.startsWith("/api/monitors")) return route.fulfill({ json: { dashboard: { ok: true, data: { items: [] } } } });
    return route.fulfill({ json: { items: [] } });
  });
  return { chartReads, writes, release };
}

async function open(page: Page, instrument = INSTRUMENT) {
  await page.goto(`/charts?instrument_id=${encodeURIComponent(instrument)}&subject_id=${SUBJECT}`);
  await expect(page.getByRole("heading", { name: "Charts", exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: /Instrument ID/ })).toHaveValue(instrument);
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

test("long weekly chart exposes SMC Limitations and source warnings with ATR200 ready", async ({ page }) => {
  const daily = scene();
  const bars = daily.bars.map((bar, index) => ({ ...bar, timestamp: new Date(Date.UTC(2021, 0, 4, 21) + index * 7 * 86400000).toISOString() }));
  const weekly = {
    ...daily, bars_interval: "1w", bars, as_of: bars.at(-1)!.timestamp,
    timeframes: [{ ...daily.timeframes[0], interval: "1w", smart_money: {
      ...daily.timeframes[0].smart_money, atr_200_ready: true,
      limitations: ["SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL", "SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE", "SMC_ADDITIONAL_SOURCE_LIMITATION"],
      structure_events: [], order_blocks: [], liquidity_levels: [],
    } }],
  };
  const api = await mock(page, false, { data: weekly, warnings: [
    { code: "TECHNICAL_WEEKLY_BOUNDARIES_MAY_BE_PARTIAL", message: "Weekly boundaries may be partial." },
    { code: "US_NON_SESSION_DAILY_BAR", message: "Provider supplied this warning code." },
    { code: "SOURCE_COVERAGE_LIMIT", message: "Only part of the requested history is available." },
  ] });
  await open(page);
  await page.getByRole("combobox", { name: /Chart Period/ }).selectOption("1w");
  await page.getByRole("button", { name: "Open Chart", exact: true }).click();
  await expect(page.getByRole("img", { name: /interactive candlestick chart/ })).toBeVisible();
  await expect(page.getByText("260 bars", { exact: true })).toBeVisible();
  const smc = page.getByRole("note", { name: "SMC Limitations" });
  await expect(smc.getByText(/structures involving an unfinished week remain provisional/)).toBeVisible();
  await expect(smc.getByText(/Percentage-based fair-value gaps are unavailable/)).toBeVisible();
  await expect(smc.getByText(/An additional SMC limitation applies/)).toBeVisible();
  const notes = page.getByRole("note", { name: "Chart Source Notes" });
  await expect(notes.getByText(/first and last weeks may be incomplete/)).toBeVisible();
  await expect(notes.getByText(/outside exchange sessions/)).toBeVisible();
  await expect(notes.getByText("Only part of the requested history is available.")).toBeVisible();
  await expect(page.getByText("split and dividend adjusted daily close", { exact: true })).toBeVisible();
  expect(api.chartReads[0]).toMatchObject({ arguments: { intervals: ["1w"], lookback_sessions: 1000 } });
  expect(api.writes).toEqual([]);
  await page.getByRole("textbox", { name: /Instrument ID/ }).fill("equity:US:OTHER");
  await expect(smc).toHaveCount(0);
  await expect(notes).toHaveCount(0);
});

test("daily chart keeps envelope source warnings without adding weekly limitations", async ({ page }) => {
  const api = await mock(page, false, { data: scene(), warnings: [
    { code: "LATEST_ADJUSTED_BAR_UNAVAILABLE", message: "Provider supplied this warning code." },
    { code: "TECHNICAL_DATA_NOT_FRESH", message: "Provider supplied this warning code." },
  ] });
  await open(page);
  await page.getByRole("button", { name: "Open Chart", exact: true }).click();
  const notes = page.getByRole("note", { name: "Chart Source Notes" });
  await expect(notes.getByText(/newest adjusted daily bar is unavailable/)).toBeVisible();
  await expect(notes.getByText(/Check the source timestamp/)).toBeVisible();
  await expect(page.getByRole("note", { name: "SMC Limitations" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "SMC", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(api.chartReads[0]).toMatchObject({ arguments: { intervals: ["1d"], lookback_sessions: 500 } });
  expect(api.writes).toEqual([]);
});

test("weekly chart handoff retains mapped limitations and source caveats in the reviewed draft", async ({ page }) => {
  const daily = scene();
  const bars = daily.bars.map((bar, index) => ({ ...bar, timestamp: new Date(Date.UTC(2021, 0, 4, 21) + index * 7 * 86400000).toISOString() }));
  const weekly = {
    ...daily, bars_interval: "1w", bars, as_of: bars.at(-1)!.timestamp,
    timeframes: [{ ...daily.timeframes[0], interval: "1w", smart_money: {
      ...daily.timeframes[0].smart_money, atr_200_ready: true,
      limitations: ["SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL", "SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE"],
      structure_events: [{ scope: "swing", event: "bos", direction: "bullish", occurred_at: bars[210].timestamp, price: "100" }],
      liquidity_levels: [], order_blocks: [],
    } }],
  };
  const api = await mock(page, false, { data: weekly, warnings: [
    { code: "TECHNICAL_DATA_NOT_FRESH", message: "Provider supplied this warning code." },
    { code: "SOURCE_COVERAGE_LIMIT", message: "Only part of the requested history is available." },
  ] });
  await open(page);
  await page.getByRole("combobox", { name: /Chart Period/ }).selectOption("1w");
  await page.getByRole("button", { name: "Open Chart", exact: true }).click();
  await expect(page.getByRole("img", { name: /interactive candlestick chart/ })).toBeVisible();
  await page.getByRole("combobox", { name: /Inspect Structure/ }).selectOption("event-0");
  await page.getByRole("button", { name: "Use as Research Context" }).click();
  await page.getByLabel("Your Interpretation").fill(NOTE);
  await page.getByRole("button", { name: "Continue in Research" }).click();
  await expect(page.getByRole("heading", { name: "Review Chart Context" })).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: "Chart Caveats:" })).toContainText("remain provisional");
  await expect(page.locator("pre").filter({ hasText: "Chart Caveats:" })).toContainText("Only part of the requested history is available.");
  expect(api.writes).toEqual([]);
  await page.getByRole("button", { name: "Accept Context into Draft" }).click();
  const editor = page.getByRole("region", { name: "Create Thesis", exact: true });
  await expect(editor.getByLabel("Candidate Status")).toHaveValue("draft");
  await expect(editor.getByRole("textbox", { name: /Statement/ })).toHaveValue(NOTE);
  const rationale = await editor.getByRole("textbox", { name: /Rationale/ }).inputValue();
  for (const expected of ["Source interval: 1w", "Status at snapshot: confirmed", bars[210].timestamp,
    "structures involving an unfinished week remain provisional", "Percentage-based fair-value gaps are unavailable",
    "Check the source timestamp", "Only part of the requested history is available.",
    "SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL", "TECHNICAL_DATA_NOT_FRESH"]) expect(rationale).toContain(expected);
  expect(new URL(page.url()).searchParams.has("chart_context")).toBe(false);
  expect(api.writes).toEqual([]);
});

test("date-label futures disable historical cutoff while retaining explicitly reviewed snapshot context", async ({ page }) => {
  const instrument = "future:US:SI=F";
  const limitation = "SMC_FUTURES_DATE_LABEL_CONFIRMATION_TIME_UNKNOWN";
  const sourceWarning = "FUTURES_DAILY_DATE_ONLY_COMPLETION_UNKNOWN";
  const original = scene();
  const bars = original.bars.map((bar) => ({ ...bar, timestamp: `${bar.timestamp.slice(0, 10)}T00:00:00-04:00` }));
  const label = bars.at(-1)!.timestamp;
  const native = {
    ...original, instrument_id: instrument,
    price_basis: "unadjusted_front_month_continuous_futures_close", bars,
    as_of: new Date(Date.parse(label) + 86400000).toISOString(),
    timeframes: [{ ...original.timeframes[0], smart_money: {
      ...original.timeframes[0].smart_money, limitations: [limitation],
      structure_events: [{ scope: "internal", event: "bos", direction: "bullish", occurred_at: label,
        broken_swing_at: bars.at(-6)!.timestamp, price: "100" }],
      swings: [], liquidity_levels: [], order_blocks: [], fair_value_gaps: [], value_zones: [],
    } }],
  };
  const api = await mock(page, false, { data: native, instrument, warnings: [
    { code: sourceWarning, message: "Provider supplied this warning code." },
  ] });
  await open(page, instrument);
  expect(api.chartReads).toHaveLength(0);
  await page.getByRole("button", { name: "Open Chart", exact: true }).click();
  await expect(page.getByRole("img", { name: /interactive candlestick chart/ })).toBeVisible();
  await expect(page.getByLabel("Historical Cutoff (UTC)")).toBeDisabled();
  await expect(page.getByLabel("Historical Cutoff (UTC)")).toHaveValue("");
  await expect(page.getByRole("note", { name: "SMC Limitations" })).toContainText("not verified availability times");
  await expect(page.getByText("260 bars", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: /Inspect Structure/ }).selectOption("event-0");
  await expect(page.getByText(`Occurrence label ${label} · Confirmation label ${label}`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Use as Research Context" }).click();
  await page.getByLabel("Your Interpretation").fill(NOTE);
  await page.getByRole("button", { name: "Continue in Research" }).click();
  await expect(page.getByRole("heading", { name: "Review Chart Context" })).toBeVisible();
  const staged = await page.evaluate(() => {
    const id = new URL(window.location.href).searchParams.get("chart_context");
    return JSON.parse(sessionStorage.getItem(`tp:chart-research:${id}`)!);
  });
  expect(staged).toMatchObject({ instrument_id: instrument, cutoff: null,
    structure: { occurred_at: label, confirmed_at: label } });
  expect(staged.disclosures.map((entry: { code: string }) => entry.code)).toEqual(expect.arrayContaining([limitation, sourceWarning]));
  const provenance = page.locator("pre").filter({ hasText: "Chart Caveats:" });
  await expect(provenance).toContainText("Cutoff: snapshot");
  await expect(provenance).toContainText(`Confirmation label: ${label}`);
  await expect(provenance).toContainText("do not establish observation or confirmation availability times");
  expect(api.writes).toEqual([]);
  await page.getByRole("button", { name: "Accept Context into Draft" }).click();
  const editor = page.getByRole("region", { name: "Create Thesis", exact: true });
  await expect(editor.getByLabel("Candidate Status")).toHaveValue("draft");
  await expect(editor.getByRole("textbox", { name: /Statement/ })).toHaveValue(NOTE);
  const rationale = await editor.getByRole("textbox", { name: /Rationale/ }).inputValue();
  for (const value of [instrument, "Cutoff: snapshot", `Confirmation label: ${label}`, limitation, sourceWarning]) expect(rationale).toContain(value);
  expect(api.chartReads).toHaveLength(1);
  expect(api.writes).toEqual([]);
});
