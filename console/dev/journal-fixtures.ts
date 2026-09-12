// Synthetic UI fixtures only. Never import owner notes or account values here.
const SUBJECT = "case_00000000-0000-7000-8000-000000000001";
const DECISION = "decision_00000000-0000-7000-8000-000000000001";
const PLAN = "trade_plan_00000000-0000-7000-8000-000000000001";
const CYCLE = "trade_cycle_phase4_e2e";
const ORDER = "broker_order_00000000-0000-7000-8000-000000000001";
const REVIEW = "review_item_00000000-0000-7000-8000-000000000001";

function envelope(data: Record<string, unknown>) {
  return { ok: true, data, warnings: [], errors: [], degraded: false };
}

export function workbench() {
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
      algorithm_version: "behavior_summary_v3",
      return_basis: "NET_PNL_OVER_MAXIMUM_DEPLOYED_CAPITAL",
      closed_active_trade_cycles: { numerator: 3, denominator: 10, value: 3, excluded_count: 7, availability: "AVAILABLE" },
      wins: { numerator: 2, denominator: 3, value: 2, excluded_count: 7, availability: "AVAILABLE" },
      losses: { numerator: 1, denominator: 3, value: 1, excluded_count: 7, availability: "AVAILABLE" },
      flat: { numerator: 0, denominator: 3, value: 0, excluded_count: 7, availability: "AVAILABLE" },
      win_rate: { numerator: 2, denominator: 3, value: "0.6666666667", excluded_count: 7, availability: "AVAILABLE" },
      avg_win: { numerator: "200", denominator: 2, value: "100", excluded_count: 8, availability: "AVAILABLE", native_currencies: ["USD"] },
      avg_loss: { numerator: "-50", denominator: 1, value: "-50", excluded_count: 9, availability: "AVAILABLE", native_currencies: ["USD"] },
      payoff_ratio: { numerator: "200", denominator: 1, value: "2", excluded_count: 7, availability: "AVAILABLE", native_currencies: ["USD"] },
      avg_win_return: { numerator: "0.4", denominator: 2, value: "0.2", excluded_count: 8, availability: "AVAILABLE", native_currencies: ["USD"] },
      avg_loss_return: { numerator: "-0.05", denominator: 1, value: "-0.05", excluded_count: 9, availability: "AVAILABLE", native_currencies: ["USD"] },
      return_payoff_ratio: { numerator: "0.4", denominator: 1, value: "4", excluded_count: 7, availability: "AVAILABLE", native_currencies: ["USD"] },
      plan_coverage: { numerator: 1, denominator: 3, value: "0.3333333333", excluded_count: 7, availability: "AVAILABLE" },
      pre_fill_decision_coverage: { numerator: 2, denominator: 3, value: "0.6666666667", excluded_count: 7, availability: "AVAILABLE" },
      no_action_review_completion: { numerator: 0, denominator: 0, value: null, excluded_count: 0, availability: "UNAVAILABLE", unavailable_reason: "NO_ACTION_SAMPLE_EMPTY" },
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


export const SYNTHETIC_NOTE_SUMMARIES = [
  "关注企业长期盈利能力，而不是单日价格波动。当前仍需验证新产品带来的收入增长能否持续；在证据变化前保留现有判断，并明确记录不采取行动的理由。",
  "云业务需求仍有韧性，但资本开支上升，需要持续跟踪现金流与投资回报。",
];
