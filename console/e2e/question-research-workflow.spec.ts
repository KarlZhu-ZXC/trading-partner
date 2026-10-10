import { expect, test, type Page } from "@playwright/test";
import { RESEARCH_SAMPLE, syntheticResearchWorkspace, syntheticResearchLinks, syntheticResearchChanges } from "../dev/research-workflow-fixtures";

const links = syntheticResearchLinks();
const QUESTION = syntheticResearchWorkspace().subjects[0].state.data.open_questions[0].question_id;
const CONVERSATION = "agent_conversation_question_fixture";
const MESSAGE = "agent_message_question_answer";
const AT = "2026-10-02T12:00:00Z";
const context = { subject_id: RESEARCH_SAMPLE, question_id: QUESTION, question_version: 1,
  question_fingerprint: "a".repeat(64), evidence_cutoff: AT,
  thesis_revision_id: links.targets[0].thesis_revision_id, assumption_id: links.targets[0].assumption_id,
  source_refs: [links.sources[0].source], source_coverage: "COMPLETE" };
const prepared = { context, prompt: "Synthetic pinned question and evidence requirements", question_context_token: "synthetic-question-token", expires_at: "2027-01-01T00:00:00Z" };
const research = { mode: "research", phase: "FINISHED", stop_reason: "TIME_BUDGET",
  max_seconds: 180, max_model_calls: 8, max_tool_calls: 24, elapsed_ms: 180000,
  model_calls_attempted: 1, tool_calls_attempted: 1, completed_reads: 1,
  usage_complete: false, cost_usd: null, provider_internal_attempts: null, challenge_performed: false,
  verified_claims: 0, blocked_claims: 0, verified_refs: [], evidence_status: "STOPPED",
  gaps: [], steps: [], question_context: context };

async function mock(page: Page, changed = false) {
  const writes: Array<{ path: string; body: Record<string, unknown> }> = [];
  let messages: Array<Record<string, unknown>> = [];
  const conversation = { conversation_id: CONVERSATION, owner_principal: "local-console", title: "Question fixture", status: "ACTIVE", rolling_summary: "", summary_through_sequence: 0, next_message_sequence: 1, version: 1, created_at: AT, updated_at: AT };
  await page.route("**/api/console/**", async (route) => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname.replace("/api/console", "");
    const json = (value: unknown, status = 200) => route.fulfill({ json: value, status });
    if (req.method() === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>; writes.push({ path, body });
      if (path.endsWith("/messages/stream")) {
        messages = [{ message_id: MESSAGE, conversation_id: CONVERSATION, sequence: 1, role: "ASSISTANT", content: "Need further primary evidence.", created_at: AT, model_receipt: { research } }];
        const events = [["research_progress", { conversation_id: CONVERSATION, research }], ["completed", { conversation_id: CONVERSATION }]];
        return route.fulfill({ contentType: "text/event-stream", body: events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("") });
      }
      if (path === "/api/tools/invoke") return json({ result: { ok: true, data: { candidate_id: "candidate_prepared" }, errors: [] } });
      return json({ detail: "Unexpected write" }, 403);
    }
    if (path.endsWith("/research-context")) return json({ data: prepared });
    if (path.endsWith("/research-sources")) return json({ data: { context, context_status: changed ? "CONTEXT_CHANGED" : "CURRENT", sources: [links.sources[0]], sources_complete: true } });
    if (path === "/api/session") return json({ token: "synthetic-question-console-session-token" });
    if (path === "/api/research") return json(syntheticResearchWorkspace());
    if (path.endsWith("/evidence-links")) return json({ data: links });
    if (path.endsWith("/changes")) return json({ data: syntheticResearchChanges() });
    if (path === "/api/agent/status") return json({ enabled: true, configured: true, available: true, state: "READY", diagnostics: [], default_model_id: "fixture", providers: [{ id: "fixture", provider: "fixture", model: "synthetic", api_style: "responses", reasoning_mode: "effort", reasoning_effort: "high", reasoning_efforts: ["high"], is_default: true }], models: [], components: {} });
    if (path === "/api/agent/providers/fixture/models") return json({ provider_id: "fixture", default_model: "synthetic", reasoning_mode: "effort", models: [{ id: "synthetic", label: "synthetic", is_default: true, reasoning_efforts: ["high"] }], fetched_at: AT });
    if (path === "/api/agent/conversations") return json({ items: [conversation] });
    if (path.endsWith("/messages")) return json({ items: messages });
    if (path.endsWith("/turns")) return json({ items: [], latest_turn: null });
    if (path.endsWith("/metrics")) return json({ metrics: { conversation_id: CONVERSATION, turn_statuses: {} } });
    return json({ items: [] });
  });
  return writes;
}

test("question preparation does not send until explicit Copilot Send and retains outcome on reload", async ({ page }) => {
  const writes = await mock(page);
  await page.goto(`/research?subject_id=${RESEARCH_SAMPLE}&section=thesis`);
  await page.getByRole("button", { name: "Research with Copilot", exact: true }).click();
  await page.getByLabel("Research Assumption").selectOption(`${context.thesis_revision_id}|${context.assumption_id}`);
  await page.getByRole("button", { name: "Prepare in Copilot", exact: true }).click();
  await expect(page.getByLabel("Message Copilot")).toHaveValue(prepared.prompt);
  expect(writes).toEqual([]);
  await page.getByRole("button", { name: "Send Message", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body.question_context_token).toBe(prepared.question_context_token);
  expect(writes[0].body.research_mode).toBe("research");
  await expect(page.getByText("Research Incomplete", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("Research Incomplete", { exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
  await page.getByRole("link", { name: "Review Prepared Sources", exact: true }).click();
  await expect(page.getByRole("region", { name: "Prepared Research Sources" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Use Source in Link Draft", exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
});

test("prepared durable source opens an editable Candidate draft and never confirms", async ({ page }) => {
  const writes = await mock(page);
  await page.goto(`/research?subject_id=${RESEARCH_SAMPLE}&section=evidence&research_conversation_id=${CONVERSATION}&research_message_id=${MESSAGE}`);
  await page.getByRole("button", { name: "Use Source in Link Draft", exact: true }).click();
  expect(writes).toEqual([]);
  await expect(page.locator("#research-section-evidence-impact").getByLabel("Rationale")).toHaveValue("");
  await page.locator("#research-section-evidence-impact").getByLabel("Rationale").fill("I reviewed the original record against the exact assumption.");
  await page.getByRole("button", { name: "Propose Candidate", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body.tool_name).toBe("research_judgment_propose");
  expect(JSON.stringify(writes[0].body)).toContain(context.thesis_revision_id);
});

test("changed question keeps its old source context visible but blocks handoff", async ({ page }) => {
  const writes = await mock(page, true);
  await page.goto(`/research?subject_id=${RESEARCH_SAMPLE}&section=evidence&research_conversation_id=${CONVERSATION}&research_message_id=${MESSAGE}`);
  await expect(page.getByText(/question or selected assumption changed/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Use Source in Link Draft", exact: true })).toBeDisabled();
  expect(writes).toEqual([]);
});
