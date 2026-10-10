import { NextResponse } from "next/server";
import { workbench, SYNTHETIC_NOTE_SUMMARIES } from "../../../../dev/journal-fixtures";
import { syntheticTechnicalChartScene } from "../../../../dev/chart-fixtures";
import { syntheticDailyReview } from "../../../../dev/daily-review-fixtures";
import {
  isSyntheticSubject,
  syntheticDecisionReplay,
  syntheticEmptyQuickReview,
  syntheticEmptyResearchChanges,
  syntheticQuickReview,
  syntheticResearchChanges,
  syntheticResearchLinks,
  syntheticResearchWorkspace,
} from "../../../../dev/research-workflow-fixtures";

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
  if (route === "research") return reply(syntheticResearchWorkspace());
  const researchPath = route.split("/");
  if (researchPath[0] === "research" && researchPath.length > 1) {
    const subjectId = researchPath[1];
    if (!isSyntheticSubject(subjectId)) return reply({ detail: "Synthetic Research Subject not found" }, 404);
    const previewState = query.get("preview_state");
    if (researchPath[2] === "evidence-links" && researchPath.length === 3) {
      if (previewState === "error") return reply({ detail: "Synthetic sources unavailable" }, 500);
      if (previewState === "empty") {
        return reply({ data: { ...syntheticResearchLinks(subjectId), sources: [], targets: [], impacts: [], impact_history: [], question_history: [], decisions: [], coverage: { sources: "COMPLETE", targets: "COMPLETE", impacts: "COMPLETE", impact_history: "COMPLETE", decisions: "COMPLETE", question_history: "COMPLETE" } } });
      }
      return reply({ data: syntheticResearchLinks(subjectId) });
    }
    if (researchPath[2] === "quick-review" && researchPath.length === 3) {
      if (previewState === "error") return reply({ detail: "Synthetic Quick Review unavailable" }, 500);
      return reply({ data: previewState === "empty" ? syntheticEmptyQuickReview(subjectId) : syntheticQuickReview(subjectId) });
    }
    if (researchPath[2] === "quick-review" && researchPath[3] === "submissions" && researchPath.length === 5) {
      return reply({ data: { status: "NOT_FOUND" } });
    }
    if (researchPath[2] === "changes" && researchPath.length === 3) {
      if (previewState === "error") return reply({ detail: "Synthetic changes unavailable" }, 500);
      const baselineDecisionId = query.get("baseline_decision_id");
      return reply({ data: previewState === "empty" ? syntheticEmptyResearchChanges(subjectId) : syntheticResearchChanges(subjectId, baselineDecisionId) });
    }
    if (researchPath[2] === "decisions" && researchPath[3] && researchPath[4] === "replay" && researchPath.length === 5) {
      try {
        if (previewState === "error") return reply({ detail: "Synthetic replay unavailable" }, 500);
        const replay = syntheticDecisionReplay(subjectId, researchPath[3]);
        if (previewState === "empty") return reply({ data: { ...replay, revisions: [], sources: [], impacts: [], questions: [], coverage: { revisions: "COMPLETE", sources: "COMPLETE", impacts: "COMPLETE", questions: "COMPLETE" }, warnings: [] } });
        return reply({ data: replay });
      } catch {
        return reply({ detail: "Synthetic Decision not found for this Research Subject" }, 404);
      }
    }
    return reply({ detail: "Synthetic Research route not found" }, 404);
  }
  if (["review-digest", "weekly-review", "overview"].includes(route) && query.get("preview_state") === "error") return reply({ detail: "Synthetic read failure — use the preview controls to recover." }, 500);
  if (route === "review-digest") {
    const digest = syntheticDailyReview();
    if (query.get("preview_state") === "empty") { digest.groups = []; digest.unscoped = []; }
    return reply({ data: digest });
  }
  if (route === "weekly-review") return reply({ data: { as_of: "2026-09-22T08:00:00Z", week_start: "2026-09-20T16:00:00Z", week_end: "2026-09-27T16:00:00Z", timezone: "Asia/Shanghai", confirmed_views: [], decisions: [], open_questions: [], due_groups: [], unresolved_groups: [], coverage: { research: "COMPLETE" }, warnings: [] } });
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

export async function POST(request: Request, context: Context) {
  if (process.env.CONSOLE_DESIGN_PREVIEW !== "1") return reply({ detail: "Not found" }, 404);
  const { path } = await context.params;
  const route = path.join("/").replace(/^api\//, "");
  if (route === "tools/invoke") {
    const payload = await request.json() as { tool_name?: unknown; arguments?: { include_bars?: unknown } };
    if (payload.tool_name === "technical_get_snapshot" && payload.arguments?.include_bars === true) {
      return reply({ tool_name: payload.tool_name, result: { ok: true, data: syntheticTechnicalChartScene(), warnings: [], errors: [], degraded: false } });
    }
  }
  return reply({ detail: "This preview uses synthetic data. Operational writes are disabled." }, 403);
}
