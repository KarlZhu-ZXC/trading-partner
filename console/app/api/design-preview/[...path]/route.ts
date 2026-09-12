import { NextResponse } from "next/server";
import { workbench, SYNTHETIC_NOTE_SUMMARIES } from "../../../../dev/journal-fixtures";

type Dict = Record<string, unknown>;
type Context = { params: Promise<{ path: string[] }> };
const reply = (value: unknown, status = 200) => NextResponse.json(value, { status, headers: { "Cache-Control": "no-store" } });

export async function GET(request: Request, context: Context) {
  if (process.env.CONSOLE_DESIGN_PREVIEW !== "1") return reply({ detail: "Not found" }, 404);
  const { path } = await context.params;
  const route = path.join("/").replace(/^api\//, "");
  const query = new URL(request.url).searchParams;
  const data = workbench();
  const subject = data.subjects[0].subject;
  subject.title = "AI Infrastructure · Research";
  subject.summary = "Track durable business performance, changing expectations, and the evidence behind each decision.";
  data.accounts.data.accounts = [
    { account_ref: "account_1", provider: "schwab", positions: [] },
    { account_ref: "account_2", provider: "schwab", positions: [] },
  ];
  data.trade_cycles.data.status = "INCOMPLETE";
  const cycles = data.trade_cycles.data.cycles as Dict[];
  cycles.forEach((cycle, index) => { cycle.account_ref = index % 2 ? "account_2" : "account_1"; cycle.provider = "schwab"; cycle.maximum_deployed_capital = "4800"; });
  const notes = data.external_notes as Dict[];
  const first = notes[0];
  (first.identity as Dict).title = "Apple · Long-term investment view";
  (first.revision as Dict).summary = SYNTHETIC_NOTE_SUMMARIES[0];
  notes.push({ ...first, identity: { ...(first.identity as Dict), note_id: "sample_note_msft", title: "Microsoft · Cloud investment cycle", primary_instrument_id: "equity:US:MSFT" }, revision: { ...(first.revision as Dict), note_revision_id: "sample_revision_msft", summary: SYNTHETIC_NOTE_SUMMARIES[1] } });
  if (route === "account-aliases") return reply({ aliases: { account_1: "Schwab IRA", account_2: "Schwab Brokerage" } });
  if (route === "session") return reply({ token: "synthetic-preview-session-000000000000000000000000" });
  if (route === "decision-workbench") {
    if (query.get("preview_state") === "error") return reply({ detail: "Synthetic read failure — use the preview controls to recover." }, 500);
    if (query.get("preview_state") === "empty") {
      data.transactions.data.transactions = []; data.trade_cycles.data.cycles = []; data.external_notes = [];
    }
    const selected = query.getAll("account_refs");
    const start = query.get("behavior_start"); const end = query.get("behavior_end");
    data.behavior.data.cohort = { start, end };
    if (selected.length) data.behavior.data.win_rate = { numerator: 1, denominator: 3, value: 1 / 3, availability: "AVAILABLE", excluded_count: 0 };
    return reply(data);
  }
  if (route === "observations") return reply({ data: { external_notes: query.get("preview_state") === "empty" ? [] : notes, observation_sources: data.observation_sources } });
  if (route === "current-view") return reply({ data: { source_title: "Apple · Long-term investment view", source_note_version: 2, source_note_revision_id: "sample_revision_2", source_note: { version: 2 }, subject_title: subject.title, review: { status: "NO_ACTION" }, decision: { title: "Wait for clearer evidence", rationale: "No new position change until the business assumptions are reviewed." }, thesis: { title: "Durable earnings growth", statement: "Watch the relationship between demand, reinvestment and cash generation." }, trade_plan: { plan_id: "sample_plan", version: 2, status: "ACTIVE", instrument_id: "equity:US:AAPL" } } });
  if (route === "overview") return reply({});
  if (route.startsWith("agent/")) return reply({ enabled: false, configured: false, available: false, state: "DISABLED", diagnostics: [], providers: [], models: [], components: {} });
  if (route.includes("history")) return reply({ data: { revisions: [] } });
  return reply({ items: [] });
}

export async function POST() {
  if (process.env.CONSOLE_DESIGN_PREVIEW !== "1") return reply({ detail: "Not found" }, 404);
  return reply({ detail: "This preview uses synthetic data. Operational writes are disabled." }, 403);
}
