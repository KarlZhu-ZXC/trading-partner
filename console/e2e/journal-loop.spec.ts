import { expect, test, type Page, type Route } from "@playwright/test";

const SUBJECT = "case_00000000-0000-7000-8000-000000000001";
const DECISION = "decision_00000000-0000-7000-8000-000000000001";
const PLAN = "trade_plan_00000000-0000-7000-8000-000000000001";
const CYCLE = "trade_cycle_phase4_e2e";
const ORDER = "broker_order_00000000-0000-7000-8000-000000000001";
const REVIEW = "review_item_00000000-0000-7000-8000-000000000001";

function envelope(data: Record<string, unknown>) {
  return { ok: true, data, warnings: [], errors: [], degraded: false };
}

function workbench() {
  return {
    selected_subject_id: SUBJECT,
    subjects: [{
      subject: {
        subject_id: SUBJECT,
        subject_type: "company",
        title: "Phase 4 Console E2E",
        summary: "Exercise the complete local Journal loop.",
        primary_instrument_id: "equity:US:AAPL",
        status: "ACTIVE",
      },
      state: envelope({
        theses: [{ thesis_id: "thesis_1", title: "AAPL structure", status: "ACTIVE", role: "PRIMARY" }],
        latest_revisions: [{ thesis_id: "thesis_1", statement: "Structure remains valid." }],
        pending_candidates: [],
        open_questions: [],
        current_trade_plan: {
          plan_id: PLAN,
          version: 1,
          instrument_id: "equity:US:AAPL",
          status: "ACTIVE",
          conditions: [],
        },
      }),
    }],
    subject_list: { total: 1, page_size: 200, ok: true },
    monitors: envelope({ items: [] }),
    agenda: envelope({ items: [] }),
    timeline: envelope({
      items: [{
        entity_type: "decision",
        entity_id: DECISION,
        title: "Wait for confirmation",
        summary: "Recorded before the Fill.",
        occurred_at: "2026-08-18T13:00:00Z",
      }],
    }),
    accounts: envelope({ accounts: [] }),
    transactions: envelope({
      transactions: [{
        provider: "schwab",
        account_ref: "account_1",
        provider_transaction_id: "fill-buy",
        instrument_id: "equity:US:AAPL",
        kind: "TRADE",
        side: "BUY",
        quantity: "10",
        price: "100",
        currency: "USD",
        occurred_at: "2026-08-18T14:00:00Z",
      }, {
        provider: "schwab",
        account_ref: "account_1",
        provider_transaction_id: "fill-msft",
        instrument_id: "equity:US:MSFT",
        kind: "TRADE",
        side: "BUY",
        quantity: "1",
        price: "500",
        currency: "USD",
        occurred_at: "2026-08-18T15:00:00Z",
      }],
    }),
    trade_cycles: envelope({
      status: "COMPLETE",
      cycles: [{
        cycle_id: CYCLE,
        instrument_id: "equity:US:AAPL",
        currency: "USD",
        activity_ids: ["fill-buy", "fill-sell"],
        opened_at: "2026-08-18T14:00:00Z",
        closed_at: "2026-08-19T14:00:00Z",
        status: "CLOSED",
        classification: "ACTIVE_TRADE",
        net_realized_pnl: "98",
        ending_quantity: "0",
        add_count: 0,
        reduce_count: 1,
        quality: "COMPLETE",
      }, ...Array.from({ length: 9 }, (_, index) => ({
        cycle_id: `trade_cycle_responsive_${index}`,
        instrument_id: "equity:US:AAPL",
        currency: "USD",
        activity_ids: [],
        opened_at: `2026-08-${String(index + 1).padStart(2, "0")}T14:00:00Z`,
        closed_at: `2026-08-${String(index + 2).padStart(2, "0")}T14:00:00Z`,
        status: index === 0 ? "OPEN" : index === 1 ? "UNRESOLVED" : "CLOSED",
        classification: "ACTIVE_TRADE",
        net_realized_pnl: String(index + 1),
        ending_quantity: "0",
        add_count: 0,
        reduce_count: 1,
        quality: index === 1 ? "INCOMPLETE" : "COMPLETE",
      }))],
      override_revisions: [],
    }),
    performance_series: envelope({
      series: [{
        account_ref: "account_1",
        currency: "USD",
        twr: "0.01",
        xirr: "0.02",
        maximum_drawdown: "-0.005",
        status: "COMPLETE",
        dividends: "0",
        interest: "0",
        known_fees: "2",
        cycle_performance: [],
      }],
    }),
    daily_equity: envelope({
      journal_activation_at: "2026-08-18T00:00:00Z",
      items: [{ quality_status: "COMPLETE" }, { quality_status: "COMPLETE" }],
    }),
    behavior: envelope({
      algorithm_version: "behavior_summary_v1",
      closed_active_trade_cycles: { numerator: 3, denominator: 10, value: 3, excluded_count: 7, sample_sufficient: true, availability: "AVAILABLE" },
      wins: { numerator: 2, denominator: 3, value: 2, excluded_count: 7, sample_sufficient: true, availability: "AVAILABLE" },
      losses: { numerator: 1, denominator: 3, value: 1, excluded_count: 7, sample_sufficient: true, availability: "AVAILABLE" },
      flat: { numerator: 0, denominator: 3, value: 0, excluded_count: 7, sample_sufficient: true, availability: "AVAILABLE" },
      win_rate: { numerator: 2, denominator: 3, value: "0.6666666667", excluded_count: 7, sample_sufficient: true, availability: "AVAILABLE" },
      avg_win: { numerator: "200", denominator: 2, value: "100", excluded_count: 8, sample_sufficient: true, availability: "AVAILABLE", native_currencies: ["USD"] },
      avg_loss: { numerator: "-50", denominator: 1, value: "-50", excluded_count: 9, sample_sufficient: true, availability: "AVAILABLE", native_currencies: ["USD"] },
      payoff_ratio: { numerator: "200", denominator: 1, value: "2", excluded_count: 7, sample_sufficient: true, availability: "AVAILABLE", native_currencies: ["USD"] },
      plan_coverage: { numerator: 1, denominator: 3, value: "0.3333333333", excluded_count: 7, sample_sufficient: true, availability: "AVAILABLE" },
      pre_fill_decision_coverage: { numerator: 2, denominator: 3, value: "0.6666666667", excluded_count: 7, sample_sufficient: true, availability: "AVAILABLE" },
      no_action_review_completion: { numerator: 0, denominator: 0, value: null, excluded_count: 0, sample_sufficient: false, availability: "UNAVAILABLE", unavailable_reason: "NO_ACTION_SAMPLE_EMPTY" },
    }),
    retro: envelope({ runs: [] }),
    scorecards: envelope({ runs: [] }),
    partial_failures: [],
    review_items: [{
      review_item_id: REVIEW,
      source_key: "retro:test",
      source_ref: "retro_test",
      source_type: "TRADE_RETRO",
      subject_id: SUBJECT,
      title: "Review exact period finding",
      detail: "A durable finding still needs human review.",
      severity: "ATTENTION",
      status: "OPEN",
      version: 1,
      href: "#reviews",
    }],
    review_item_metrics: { open_count: 1, acknowledged_count: 0, total_items: 1 },
    activity_annotations: [],
    order_intents: [{
      order_intent_id: ORDER,
      case_id: SUBJECT,
      decision_id: DECISION,
      trade_plan_id: PLAN,
      trade_plan_version: 1,
      instrument_id: "equity:US:AAPL",
      account_ref: "account_1",
      instruction: "BUY",
      quantity: 10,
      order_type: "LIMIT",
      limit_price: "100",
      status: "SUBMITTED",
      broker_order_id: "schwab-order-1",
      created_at: "2026-08-18T13:55:00Z",
      submitted_at: "2026-08-18T13:56:00Z",
    }],
    behavior_review_runs: [],
    external_notes: [{
      identity: {
        note_id: "external_note_aapl",
        title: "AAPL Living Note",
        primary_instrument_id: "equity:US:AAPL",
      },
      revision: {
        note_revision_id: "external_note_revision_aapl_2",
        version: 2,
        coverage: "FULL",
        observed_at: "2026-08-27T12:00:00Z",
        summary: "AAPL remains near the top of its range.",
        blocks: [
          { ordinal: 0, speaker_label: "USER", body: "08/28" },
          { ordinal: 1, speaker_label: "USER", body: "Range-top observation." },
          { ordinal: 2, speaker_label: "USER", body: "Wait for confirmation." },
          { ordinal: 3, speaker_label: "External Analyst", body: "Outside viewpoint." },
        ],
      },
      interpretation: {
        status: "SUCCEEDED",
        payload: {
          material_change_summary: "Range-top evidence remains inconclusive.",
          viewpoints: [{
            speaker_label: "USER",
            direction: "SIDEWAYS",
            summary: "No confirmed breakout yet.",
          }],
          user_scenarios: [
            { scenario: "UPSIDE", action: "REVIEW", condition: "Confirm breakout." },
            { scenario: "SIDEWAYS", action: "NO_ACTION", condition: "Remain in range." },
            { scenario: "PULLBACK", action: "REVIEW", condition: "Reassess support." },
            { scenario: "INVALIDATION", action: "REVIEW", condition: "Exit thesis range." },
          ],
        },
      },
    }],
    observation_sources: [
      { source_code: "MOOMOO_NOTE", display_name: "Moomoo Private Notes" },
      { source_code: "LOCAL_OBSERVATION_BRIDGE", display_name: "Local Observation Bridge" },
    ],
  };
}

async function mockApi(page: Page) {
  const writes: Array<{ path: string; body: unknown }> = [];
  let deepReviewed = false;
  await page.route("**/api/console/api/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    const json = (value: unknown) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(value),
    });
    if (path === "/api/session") return json({ token: "e2e-session-token-0000000000000000" });
    if (path.startsWith("/api/decision-workbench")) return json(workbench());
    if (path === "/api/observations") {
      const value = workbench();
      return json({ data: {
        external_notes: value.external_notes,
        observation_sources: value.observation_sources,
      } });
    }
    if (path === "/api/observations/external_note_revision_aapl_2/review") return json({ data: {
      material_change_summary: deepReviewed ? "Max review confirms that range-top evidence remains inconclusive." : "Range-top evidence remains inconclusive.",
      thesis: { statement: "Wait for a confirmed breakout." },
      latest_decision: { title: "No action while range-bound" },
      deterministic_flags: ["REVIEW_THESIS_IMPACT"],
      requires_deep_review: true,
      deep_review: deepReviewed ? { status: "SUCCEEDED", model: "qwen3.8-max", payload: {
        material_change_summary: "Max review confirms that range-top evidence remains inconclusive.",
        user_scenarios: [
          { scenario: "UPSIDE", action: "REVIEW", condition: "Confirm breakout." },
          { scenario: "SIDEWAYS", action: "NO_ACTION", condition: "Remain in range." },
          { scenario: "PULLBACK", action: "REVIEW", condition: "Reassess support." },
          { scenario: "INVALIDATION", action: "REVIEW", condition: "Exit thesis range." },
        ],
      } } : null,
    } });
    if (path === "/api/current-view") return json({ data: {
      subject_title: "Phase 4 Console E2E",
      source_note_revision_id: "external_note_revision_aapl_1",
      review: { status: "NO_ACTION" },
      decision: { title: "Wait for confirmation", rationale: "Range evidence is incomplete." },
      thesis: { title: "AAPL primary", statement: "Wait for a confirmed breakout." },
      trade_plan: { plan_id: "trade_plan_1", version: 2, status: "ACTIVE", instrument_id: "equity:US:AAPL" },
    } });
    if (path === "/api/retro") return json({ console_windows: { previous: { start: "2026-08-17T00:00:00Z", end: "2026-08-22T00:00:00Z" }, next: { start: "2026-08-31T00:00:00Z", end: "2026-09-05T00:00:00Z" } } });
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
    if (request.method() === "POST") {
      writes.push({ path, body: request.postDataJSON() });
      if (path === "/api/tools/invoke") return json({ result: { ok: true, data: { decision_id: "decision_note_review" } } });
      if (path.endsWith("/review/ensure")) return json({ data: { review_id: "external_note_review_aapl_2", version: 1, status: "PENDING" } });
      if (path.endsWith("/deep-review")) { deepReviewed = true; return json({ data: { status: "SUCCEEDED", model: "qwen3.8-max" } }); }
      if (path === "/api/observation-reviews/external_note_review_aapl_2") return json({ data: { review_id: "external_note_review_aapl_2", version: 2, status: "NO_ACTION", decision_id: "decision_note_review" } });
      if (path === "/api/trade-cycle-overrides/preview") return json({ impacts: [{ operation: "SPLIT" }] });
      if (path === "/api/trade-cycle-overrides") return json({ version: 1 });
      if (path === "/api/behavior-reviews") return json({ status: "COMPLETE" });
    }
    return json({ items: [] });
  });
  return writes;
}

test("Journal Console connects Decision, Timeline, Cycle preview, and Review", async ({ page }) => {
  const writes = await mockApi(page);
  await page.goto(`/decision-workbench?subject_id=${SUBJECT}`);
  await expect(page.getByRole("heading", { name: "Phase 4 Console E2E" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Data Confidence" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Current Confirmed View" })).toBeVisible();
  const tradedInstruments = page.locator(".journal-instrument-card");
  await expect(tradedInstruments.getByRole("heading", { name: "Traded Instruments" })).toBeVisible();
  await expect(tradedInstruments.locator("tbody tr")).toHaveCount(2);
  const tradedInstrumentFilter = tradedInstruments.getByRole("combobox", { name: "Instrument Filter" });
  await tradedInstrumentFilter.fill("MSFT");
  await tradedInstruments.getByRole("option", { name: /^MSFT/ }).click();
  await expect(tradedInstruments.locator("tbody tr")).toHaveCount(1);
  await expect(tradedInstruments.locator("tbody tr")).toContainText("MSFT");
  const lastTradeHeader = tradedInstruments.locator("th").filter({ hasText: "Last Trade" });
  await expect(lastTradeHeader).toHaveAttribute("aria-sort", "descending");
  await lastTradeHeader.getByRole("button").click();
  await expect(lastTradeHeader).toHaveAttribute("aria-sort", "ascending");
  await tradedInstruments.getByRole("button", { name: /Remove MSFT/ }).click();

  const instrumentFilter = page.getByRole("combobox", { name: "Instrument", exact: true });
  await instrumentFilter.fill("AAPL");
  await page.getByRole("option", { name: /^AAPL/ }).click();
  await expect(page.getByRole("button", { name: "Remove AAPL" })).toBeVisible();
  await instrumentFilter.fill("MSFT");
  await page.getByRole("option", { name: /^MSFT/ }).click();
  await expect(page.getByRole("button", { name: "Remove AAPL" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove MSFT" })).toBeVisible();
  await instrumentFilter.fill("NOT_A_REAL_INSTRUMENT");
  await expect(page.getByText("No matching suggestions.")).toBeVisible();
  await instrumentFilter.press("Escape");

  await page.getByRole("tab", { name: "Behavior" }).click();
  await page.getByRole("combobox", { name: "Period" }).selectOption("CUSTOM");
  await page.getByLabel("Start Date").fill("2026-08-01");
  await page.getByLabel("End Date").fill("2026-08-20");
  await expect(page.getByLabel("Start Date")).toHaveValue("2026-08-01");
  await expect(page.getByLabel("End Date")).toHaveValue("2026-08-20");
  await expect(page.getByText("Win Rate", { exact: true })).toHaveCount(1);
  const winRateCard = page.getByRole("article").filter({ hasText: "Win Rate" });
  await expect(winRateCard.getByText("2 ÷ 3 = 66.7%", { exact: true })).toBeVisible();
  await expect(page.getByText("$100.00 avg win ÷ $50.00 avg loss = 2.00", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: /View Inbox/ }).click();
  await expect(page.getByRole("heading", { name: "Latest Thinking" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "AAPL Living Note" })).toBeVisible();
  await expect(page.getByText("2 SOURCES")).toBeVisible();
  const attribution = page.locator(".notes-attribution-sections");
  await expect(attribution.getByText("08/28", { exact: true })).toBeVisible();
  await expect(attribution.getByText("USER", { exact: true })).toHaveCount(1);
  await expect(attribution.getByText(/Range-top observation/)).toContainText("Wait for confirmation.");

  await page.getByRole("button", { name: "Review View Change" }).click();
  const noteDecisionDialog = page.getByRole("dialog");
  await expect(noteDecisionDialog.getByLabel("Current Scenario")).toHaveValue("SIDEWAYS");
  await expect(noteDecisionDialog.getByRole("combobox").first()).toHaveValue("no_action");
  await expect(noteDecisionDialog.getByLabel("Reason")).toHaveValue(/Max review confirms that range-top evidence remains inconclusive\./);
  await expect(noteDecisionDialog.getByText("Wait for a confirmed breakout.")).toBeVisible();
  await expect(noteDecisionDialog.getByText("qwen3.8-max")).toBeVisible();
  await noteDecisionDialog.getByRole("button", { name: "Confirm & Record Decision" }).click();
  await expect.poll(() => writes.some((item) => item.path === "/api/tools/invoke")).toBe(true);
  const decisionWrite = writes.find((item) => item.path === "/api/tools/invoke" && (item.body as { tool_name?: string }).tool_name === "research_memory_append")?.body as { arguments?: { request?: { external_note_revision_id?: string } } };
  expect(decisionWrite.arguments?.request?.external_note_revision_id).toBe("external_note_revision_aapl_2");
  await expect.poll(() => writes.some(
    (item) => item.path === "/api/observation-reviews/external_note_review_aapl_2",
  )).toBe(true);

  await page.getByRole("tab", { name: "Timeline" }).click();
  await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Unlinked Activity" })).toHaveCount(0);

  await page.getByRole("tab", { name: "Trade Cycles" }).click();
  const cycleList = page.getByRole("list", { name: "Trade Cycles" });
  await expect(cycleList.getByRole("listitem")).toHaveCount(4);
  await page.setViewportSize({ width: 1280, height: 960 });
  await expect(cycleList.getByRole("listitem")).toHaveCount(8);
  await expect(cycleList.getByRole("listitem").first()).toContainText("08/19");
  const cycleSort = page.getByRole("combobox", { name: "Sort Cycles" });
  await cycleSort.selectOption("LATEST_ASC");
  await expect(cycleList.getByRole("listitem").first()).toContainText("08/01");
  await cycleSort.selectOption("LATEST_DESC");
  const statusFilter = page.getByRole("combobox", { name: "Status", exact: true });
  await statusFilter.fill("Open");
  await page.getByRole("option", { name: /^Open Position quantity/ }).click();
  await expect(cycleList.getByRole("listitem")).toHaveCount(1);
  await statusFilter.fill("Unresolved");
  await page.getByRole("option", { name: /^Unresolved/ }).click();
  await expect(cycleList.getByRole("listitem")).toHaveCount(2);
  await page.getByRole("combobox", { name: "Quality", exact: true }).selectOption("INCOMPLETE");
  await expect(cycleList.getByRole("listitem")).toHaveCount(1);
  await page.getByRole("button", { name: "Remove Open" }).click();
  await page.getByRole("button", { name: "Remove Unresolved" }).click();
  await page.getByRole("combobox", { name: "Quality", exact: true }).selectOption("ALL");
  await expect(cycleList.getByRole("listitem")).toHaveCount(8);
  await page.getByText("Cycle Adjustments", { exact: true }).click();
  const sourceCycle = page.getByRole("combobox", { name: "Source Cycle" });
  await sourceCycle.fill("08/18");
  await page.getByRole("option", { name: /AAPL · 08\/18/ }).click();
  const assignmentGroups = page.locator(".cycle-assignment-buttons");
  await assignmentGroups.nth(0).getByRole("button", { name: "Earlier Cycle" }).click();
  await assignmentGroups.nth(1).getByRole("button", { name: "Later Cycle" }).click();
  await page.getByRole("button", { name: "Preview Impact" }).click();
  await expect(page.getByText("Proposed Effective Projection", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Apply Revision" }).click();

  await page.getByRole("tab", { name: "Reviews" }).click();
  await page.getByRole("button", { name: "Acknowledge" }).click();
  const acknowledgement = page.getByRole("dialog", { name: "Acknowledge Review Item" });
  await expect(acknowledgement).toBeVisible();
  expect(writes.some((item) => item.path.includes(`/api/review-items/${REVIEW}/transition`))).toBe(false);
  await acknowledgement.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: "Preview Weekly Review" }).click();
  await page.getByRole("dialog", { name: "Create Weekly Review" }).getByRole("button", { name: "Run 3-Step Review" }).click();
  await expect.poll(() => writes.map((item) => item.path)).toEqual(expect.arrayContaining([
    "/api/trade-cycle-overrides/preview",
    "/api/trade-cycle-overrides",
    "/api/behavior-reviews",
  ]));
});
