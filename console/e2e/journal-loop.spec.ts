import { expect, test, type Page, type Route } from "@playwright/test";

const SUBJECT = "case_00000000-0000-7000-8000-000000000001";
const DECISION = "decision_00000000-0000-7000-8000-000000000001";
const PLAN = "trade_plan_00000000-0000-7000-8000-000000000001";
const CYCLE = "trade_cycle_phase4_e2e";
const ORDER = "broker_order_00000000-0000-7000-8000-000000000001";

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
      }],
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
      closed_active_trade_cycles: { numerator: 1 },
      win_rate: { numerator: 1, denominator: 1, value: "1", excluded_count: 0, sample_sufficient: true },
      payoff_ratio: { value: null },
      plan_coverage: { numerator: 1, denominator: 1, value: "1", excluded_count: 0, sample_sufficient: true },
      pre_fill_decision_coverage: { numerator: 1, denominator: 1, value: "1", excluded_count: 0, sample_sufficient: true },
      no_action_review_completion: { value: null },
    }),
    retro: envelope({ runs: [] }),
    scorecards: envelope({ runs: [] }),
    partial_failures: [],
    review_items: [],
    unlinked_activity: {
      activities: [{
        source_key: "UNLINKED_ACTIVITY:schwab:account_1:fill-buy",
        transaction: {
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
        },
      }],
      has_more: false,
      observed_complete: true,
    },
    activity_annotations: [],
    order_intents: [{
      order_intent_id: ORDER,
      case_id: SUBJECT,
      decision_id: DECISION,
      trade_plan_id: PLAN,
      trade_plan_version: 1,
      instrument_id: "equity:US:AAPL",
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
  };
}

async function mockApi(page: Page) {
  const writes: Array<{ path: string; body: unknown }> = [];
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
    if (path === "/api/agent/status") return json({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
    if (request.method() === "POST") {
      writes.push({ path, body: request.postDataJSON() });
      if (path === "/api/tools/invoke") return json({ result: { ok: true, data: {} } });
      if (path === "/api/trade-cycle-overrides/preview") return json({ impacts: [{ operation: "SPLIT" }] });
      if (path === "/api/trade-cycle-overrides") return json({ version: 1 });
      if (path === "/api/behavior-reviews") return json({ status: "COMPLETE" });
      if (path === "/api/activity-annotations") return json({ status: "LINKED_DECISION_PLAN" });
    }
    return json({ items: [] });
  });
  return writes;
}

test("Journal Console connects Decision, Fill classification, Cycle preview, and Review", async ({ page }) => {
  const writes = await mockApi(page);
  await page.goto(`/decision-workbench?subject_id=${SUBJECT}`);
  await expect(page.getByRole("heading", { name: "Phase 4 Console E2E" })).toBeVisible();
  await expect(page.getByText("Year-To-Date Returns")).toBeVisible();

  await page.getByRole("button", { name: "Record Decision" }).first().click();
  await page.getByLabel("Reason").fill("SIDEWAYS remains noisy; record NO_ACTION and review later.");
  await page.getByRole("button", { name: "Save Decision" }).click();
  await expect.poll(() => writes.some((item) => item.path === "/api/tools/invoke")).toBe(true);

  await page.getByRole("tab", { name: "Timeline" }).click();
  await page.getByLabel("Activity Classification", { exact: true }).selectOption("LINKED_DECISION_PLAN");
  await page.getByLabel("Activity Classification Type").selectOption("ACTIVE_TRADE");
  await page.getByLabel("Linked Order Intent").selectOption(ORDER);
  await page.getByRole("button", { name: "Save Classification" }).click();

  await page.getByRole("tab", { name: "Trade Cycles" }).click();
  await page.getByText("Split, Merge, or Relink Cycles").click();
  await page.getByLabel("Root Cycle").selectOption(CYCLE);
  await page.getByLabel("Source Cycles").selectOption([CYCLE]);
  await page.getByLabel("Split Group A").selectOption(["fill-buy"]);
  await page.getByLabel("Split Group B").selectOption(["fill-sell"]);
  await page.getByRole("button", { name: "Preview Impact" }).click();
  await expect(page.getByText("Preview ready", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Apply Revision" }).click();

  await page.getByRole("tab", { name: "Reviews" }).click();
  await page.getByRole("button", { name: "Run Weekly Review" }).click();
  await expect.poll(() => writes.map((item) => item.path)).toEqual(expect.arrayContaining([
    "/api/activity-annotations",
    "/api/trade-cycle-overrides/preview",
    "/api/trade-cycle-overrides",
    "/api/behavior-reviews",
  ]));
});
