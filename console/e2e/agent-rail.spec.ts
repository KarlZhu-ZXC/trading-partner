import { expect, test, type Page, type Route } from "@playwright/test";

const CONVERSATION_ID = "agent_conversation_e2e";
const TURN_ID = "agent_turn_e2e";

const conversation = {
  conversation_id: CONVERSATION_ID,
  owner_principal: "local-console",
  title: "E2E durable reconnect",
  status: "ACTIVE",
  rolling_summary: "",
  summary_through_sequence: 0,
  next_message_sequence: 1,
  version: 1,
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:00:00Z",
};

const status = {
  enabled: true,
  configured: true,
  available: true,
  state: "READY",
  diagnostics: [],
  default_model_id: "bailian",
  providers: [
    {
      id: "bailian",
      provider: "bailian",
      model: "qwen3.8-max",
      api_style: "responses",
      reasoning_mode: "effort",
      reasoning_effort: "max",
      reasoning_efforts: ["low", "medium", "high", "max"],
      native_web_search: "responses_web_search",
      is_default: true,
    },
    {
      id: "deepseek",
      provider: "deepseek",
      model: "deepseek-v4-flash",
      api_style: "chat_completions",
      reasoning_mode: "thinking",
      reasoning_effort: "max",
      reasoning_efforts: ["high", "max"],
      native_web_search: "disabled",
      is_default: false,
    },
    {
      id: "opencode_go",
      provider: "opencode_go",
      model: "deepseek-v4-flash",
      api_style: "chat_completions",
      reasoning_mode: "thinking",
      reasoning_effort: "max",
      reasoning_efforts: ["high", "max"],
      native_web_search: "disabled",
      is_default: false,
    },
    {
      id: "opencode_zen",
      provider: "opencode_zen",
      model: "gpt-5.6-luna",
      api_style: "responses",
      reasoning_mode: "effort",
      reasoning_effort: "max",
      reasoning_efforts: ["low", "medium", "high", "max"],
      native_web_search: "disabled",
      is_default: false,
    },
  ],
  models: [],
  components: {},
};

function sse(events: Array<[string, Record<string, unknown>]>): string {
  return events.map(([name, payload], index) => (
    `id: ${index + 1}\nevent: ${name}\ndata: ${JSON.stringify(payload)}\n\n`
  )).join("");
}

async function mockConsoleApi(page: Page): Promise<{
  reconnectCalls: () => number;
  failNext: () => void;
}> {
  let reconnectCount = 0;
  let failureMode = false;
  let durableMessages: Array<Record<string, unknown>> = [];
  let durableTurns: Array<Record<string, unknown>> = [];

  await page.route("**/api/console/api/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/console", "");
    const json = (value: unknown) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(value),
    });

    if (path === "/api/session") return json({ token: "e2e-session-token-0000000000000000" });
    if (path === "/api/agent/status") return json(status);
    if (path === "/api/agent/preferences") {
      return json({
        preferences: {
          preferences_id: null,
          language: "zh-CN",
          response_density: "standard",
          preferred_source_codes: [],
          risk_style: "balanced",
          default_chart: false,
          web_background: true,
          version: 0,
          updated_at: null,
        },
      });
    }
    if (path === "/api/agent/conversations") return json({ items: [conversation] });
    if (path.endsWith("/messages") && request.method() === "GET") {
      return json({ items: durableMessages });
    }
    if (path.endsWith("/receipts")) return json({ items: [] });
    if (path.endsWith("/pending-actions")) return json({ items: [] });
    if (path.endsWith("/metrics")) {
      return json({ metrics: { conversation_id: CONVERSATION_ID, turn_statuses: {} } });
    }
    if (path.endsWith("/turns")) {
      return json({ items: durableTurns, latest_turn: durableTurns[0] ?? null });
    }
    if (path === "/api/agent/providers/bailian/models") {
      return json({
        provider_id: "bailian",
        default_model: "qwen3.8-max",
        api_style: "responses",
        reasoning_mode: "effort",
        native_web_search: "responses_web_search",
        cached: false,
        fetched_at: "2026-08-20T00:00:00Z",
        models: [
          { id: "qwen3.8-max", label: "qwen3.8-max", is_default: true, reasoning_efforts: ["low", "high"] },
          { id: "qwen3.7-plus", label: "qwen3.7-plus", is_default: false, reasoning_efforts: ["low", "high"] },
        ],
      });
    }
    if (path === "/api/agent/providers/deepseek/models") {
      return json({
        provider_id: "deepseek",
        default_model: "deepseek-v4-flash",
        api_style: "chat_completions",
        reasoning_mode: "thinking",
        native_web_search: "disabled",
        cached: false,
        fetched_at: "2026-08-20T00:00:00Z",
        models: [
          { id: "deepseek-v4-flash", label: "deepseek-v4-flash", is_default: true, reasoning_efforts: ["high", "max"] },
          { id: "deepseek-v4-pro", label: "deepseek-v4-pro", is_default: false, reasoning_efforts: ["high", "max"] },
        ],
      });
    }
    if (path === "/api/agent/providers/opencode_go/models") {
      return json({
        provider_id: "opencode_go",
        default_model: "deepseek-v4-flash",
        api_style: "chat_completions",
        reasoning_mode: "thinking",
        native_web_search: "disabled",
        cached: false,
        fetched_at: "2026-08-20T00:00:00Z",
        models: [
          { id: "deepseek-v4-flash", label: "deepseek-v4-flash", is_default: true, reasoning_efforts: ["high", "max"] },
          { id: "gpt-5.6-luna", label: "gpt-5.6-luna", is_default: false, reasoning_efforts: ["low", "medium", "high", "max"] },
          { id: "muse-spark-1.2-contributor", label: "muse-spark-1.2-contributor", is_default: false, reasoning_efforts: ["low", "medium", "high", "max"] },
          { id: "qwen3.8-max", label: "qwen3.8-max", is_default: false, reasoning_efforts: ["high", "max"] },
        ],
      });
    }
    if (path === "/api/agent/providers/opencode_zen/models") {
      return json({
        provider_id: "opencode_zen",
        default_model: "gpt-5.6-luna",
        api_style: "responses",
        reasoning_mode: "effort",
        native_web_search: "disabled",
        cached: false,
        fetched_at: "2026-08-20T00:00:00Z",
        models: [
          { id: "gpt-5.6-luna", label: "gpt-5.6-luna", is_default: true, reasoning_efforts: ["low", "medium", "high", "max"] },
          { id: "gpt-5.6-sol", label: "gpt-5.6-sol", is_default: false, reasoning_efforts: ["low", "medium", "high", "max"] },
          { id: "hy3-free", label: "hy3-free", is_default: false, reasoning_efforts: [] },
          { id: "x-preview-f-free", label: "x-preview-f-free", is_default: false, reasoning_efforts: ["low", "high", "max"] },
        ],
      });
    }
    if (path.endsWith("/messages/stream") && request.method() === "POST") {
      if (failureMode) {
        durableTurns = [{
          turn_id: TURN_ID,
          conversation_id: CONVERSATION_ID,
          user_message_id: "agent_message_user_failure",
          assistant_message_id: null,
          status: "FAILED",
          error_code: "PROVIDER_RATE_LIMIT_ERROR",
          model_id: "opencode_zen",
          model: "hy3-free",
          error_http_status: 429,
          error_retryable: true,
          error_attempts: 2,
          failure_notice: {
            schema_version: 1,
            kind: "provider_request_error",
            title: "Provider Rate Limited",
            code: "PROVIDER_RATE_LIMIT_ERROR",
            provider_id: "opencode_zen",
            model: "hy3-free",
            http_status: 429,
            retryable: true,
            attempts: 2,
            explanation: "The Provider rejected the model request because its quota or shared capacity limit was reached.",
            next_action: "Retry after the Provider reset window or choose another model.",
          },
          started_at: "2026-08-20T00:00:01Z",
          updated_at: "2026-08-20T00:00:02Z",
          completed_at: "2026-08-20T00:00:02Z",
          version: 2,
        }];
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: sse([
            ["message_started", {
              conversation_id: CONVERSATION_ID,
              turn_id: TURN_ID,
              provider_id: "opencode_zen",
              model: "hy3-free",
            }],
            ["failed", {
              conversation_id: CONVERSATION_ID,
              turn_id: TURN_ID,
              code: "PROVIDER_RATE_LIMIT_ERROR",
              notification: {
                schema_version: 1,
                kind: "provider_request_error",
                title: "Provider Rate Limited",
                code: "PROVIDER_RATE_LIMIT_ERROR",
                provider_id: "opencode_zen",
                model: "hy3-free",
                http_status: 429,
                retryable: true,
                attempts: 2,
                explanation: "The Provider rejected the model request because its quota or shared capacity limit was reached.",
                next_action: "Retry after the Provider reset window or choose another model.",
              },
            }],
          ]),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: sse([
          ["message_started", { conversation_id: CONVERSATION_ID, turn_id: TURN_ID }],
          ["text_delta", { text: "partial" }],
        ]),
      });
    }
    if (path.endsWith(`/turns/${TURN_ID}/stream`)) {
      reconnectCount += 1;
      durableMessages = [
        {
          message_id: "agent_message_user_e2e",
          conversation_id: CONVERSATION_ID,
          role: "USER",
          content: "test durable reconnect",
          sequence: 1,
          created_at: "2026-08-20T00:00:01Z",
        },
        {
          message_id: "agent_message_assistant_e2e",
          conversation_id: CONVERSATION_ID,
          role: "ASSISTANT",
          content: "Recovered durable answer",
          sequence: 2,
          created_at: "2026-08-20T00:00:02Z",
        },
      ];
      durableTurns = [{
        turn_id: TURN_ID,
        conversation_id: CONVERSATION_ID,
        user_message_id: "agent_message_user_e2e",
        assistant_message_id: "agent_message_assistant_e2e",
        status: "COMPLETED",
        error_code: null,
        started_at: "2026-08-20T00:00:01Z",
        updated_at: "2026-08-20T00:00:02Z",
        completed_at: "2026-08-20T00:00:02Z",
        version: 2,
      }];
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: sse([
          ["text_delta", { text: " recovered" }],
          ["completed", { conversation_id: CONVERSATION_ID, turn_id: TURN_ID }],
        ]),
      });
    }
    return json({});
  });
  return {
    reconnectCalls: () => reconnectCount,
    failNext: () => { failureMode = true; },
  };
}

test("legacy Chat opens the shared Rail and preserves Provider-scoped choices", async ({ page }) => {
  await mockConsoleApi(page);
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/\?agent=open$/);
  await expect(page.getByRole("complementary", { name: "Copilot" })).toBeVisible();
  await expect(page.getByText("READY", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Telegram" })).toHaveCount(0);

  const provider = page.getByLabel("Copilot Provider");
  const model = page.getByLabel("Copilot Model");
  const reasoning = page.getByLabel("Reasoning Effort");
  await model.selectOption("qwen3.7-plus");
  await reasoning.selectOption("low");
  await provider.selectOption("deepseek");
  await expect(model).toHaveValue("deepseek-v4-flash");
  await model.selectOption("deepseek-v4-pro");
  await reasoning.selectOption("max");
  await provider.selectOption("bailian");
  await expect(model).toHaveValue("qwen3.7-plus");
  await expect(reasoning).toHaveValue("low");
  await provider.selectOption("opencode_go");
  await expect(model).toHaveValue("deepseek-v4-flash");
  await model.selectOption("gpt-5.6-luna");
  await reasoning.selectOption("medium");
  await provider.selectOption("bailian");
  await provider.selectOption("opencode_go");
  await expect(model).toHaveValue("gpt-5.6-luna");
  await expect(reasoning).toHaveValue("medium");
  await provider.selectOption("opencode_zen");
  await expect(model).toHaveValue("gpt-5.6-luna");
  await model.selectOption("gpt-5.6-sol");
  await provider.selectOption("opencode_go");
  await provider.selectOption("opencode_zen");
  await expect(model).toHaveValue("gpt-5.6-sol");
  await model.selectOption("x-preview-f-free");
  await expect(reasoning.locator("option")).toHaveText(["Auto", "Low", "High", "Max"]);
  await reasoning.selectOption("high");
  await expect(reasoning).toHaveValue("high");
});

test("an incomplete send stream reconnects by durable turn id without resending", async ({ page }) => {
  const api = await mockConsoleApi(page);
  await page.goto("/?agent=open");
  await expect(page.getByText("READY", { exact: true })).toBeVisible();
  await page.getByLabel("Message Copilot").fill("test durable reconnect");
  await page.getByRole("button", { name: "Send Message" }).click();
  await expect(page.getByText("Recovered durable answer", { exact: true })).toBeVisible();
  expect(api.reconnectCalls()).toBe(1);
});

test("Provider failures render a structured durable notification", async ({ page }) => {
  const api = await mockConsoleApi(page);
  api.failNext();
  await page.goto("/?agent=open");
  await expect(page.getByText("READY", { exact: true })).toBeVisible();
  await page.getByLabel("Copilot Provider").selectOption("opencode_zen");
  await page.getByLabel("Copilot Model").selectOption("hy3-free");
  await expect(page.getByLabel("Reasoning Effort")).toHaveCount(0);
  await page.getByLabel("Message Copilot").fill("trigger provider failure");
  await page.getByRole("button", { name: "Send Message" }).click();

  const notice = page.getByRole("alert", { name: "Copilot Provider Error Notification" });
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("Provider Rate Limited");
  await expect(notice).toContainText("PROVIDER_RATE_LIMIT_ERROR");
  await expect(notice).toContainText("HTTP Status");
  await expect(notice).toContainText("429");
  await expect(notice).toContainText("opencode_zen");
  await expect(notice).toContainText("hy3-free");
  await expect(notice).toContainText("Retryable");
  expect(await notice.evaluate((element) =>
    element.nextElementSibling?.classList.contains("agent-rail-scroll") ?? false)).toBe(true);

  await notice.getByRole("button", { name: "Dismiss Provider Error Notification" }).click();
  await expect(notice).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("READY", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert", { name: "Copilot Provider Error Notification" })).toHaveCount(0);
});

const researchReceipt = {
  mode: "research", phase: "FINISHED", stop_reason: "MODEL_BUDGET",
  max_seconds: 60, max_model_calls: 2, max_tool_calls: 3,
  elapsed_ms: 1400, model_calls_attempted: 2, tool_calls_attempted: 1, completed_reads: 1,
  usage_complete: false, cost_usd: null, provider_internal_attempts: null,
  challenge_performed: false, verified_claims: 2, blocked_claims: 1, verified_refs: ["request_synthetic/result/last"], evidence_status: "STOPPED", gaps: ["MODEL_BUDGET"],
  steps: [{ code: "READ", status: "COMPLETED" }, { code: "SYNTHESIZE", status: "STOPPED" }, { code: "CHALLENGE", status: "SKIPPED" }, { code: "EVIDENCE_CHECK", status: "STOPPED" }],
};

async function mockResearchTurn(page: Page, failed = false, answerContent = "Research stopped at the requested budget.") {
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  let submitted = false;
  const user = { message_id: "research_user", conversation_id: CONVERSATION_ID, role: "USER", content: "Research synthetic evidence", created_at: "2026-09-15T00:00:00Z", sequence: 1, model_receipt: { research: { ...researchReceipt, phase: "QUEUED", stop_reason: null, evidence_status: "NOT_CHECKED", verified_claims: 0, blocked_claims: 0, verified_refs: [] } } };
  const assistant = { message_id: "research_answer", conversation_id: CONVERSATION_ID, role: "ASSISTANT", content: answerContent, created_at: "2026-09-15T00:00:01Z", sequence: 2, model_receipt: { research: researchReceipt, usage: { input_tokens: 123, output_tokens: 45 }, answer_envelope: { _truncated: true } } };
  await page.route("**/api/console/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/console", "");
    const json = (value: unknown) => route.fulfill({ json: value });
    if (route.request().method() === "POST") {
      posts.push({ path, body: route.request().postDataJSON() });
      if (!path.endsWith("/messages/stream")) return route.fulfill({ status: 403, json: { error: "Unexpected write" } });
      submitted = true;
      return route.fulfill({ contentType: "text/event-stream", body: sse([
        ["message_started", { conversation_id: CONVERSATION_ID, turn_id: TURN_ID }],
        ["research_progress", { conversation_id: CONVERSATION_ID, turn_id: TURN_ID, research: { ...researchReceipt, phase: "READING", stop_reason: null } }],
        ["research_progress", { conversation_id: CONVERSATION_ID, turn_id: TURN_ID, research: researchReceipt }],
        [failed ? "failed" : "completed", { conversation_id: CONVERSATION_ID, turn_id: TURN_ID }],
      ]) });
    }
    if (path === "/api/session") return json({ token: "synthetic-research-token-000000000000" });
    if (path === "/api/agent/status") return json(status);
    if (path === "/api/agent/conversations") return json({ items: [conversation] });
    if (path.includes("/providers/") && path.endsWith("/models")) return json({ provider_id: "bailian", default_model: "qwen3.8-max", models: [{ id: "qwen3.8-max", label: "Synthetic", is_default: true, reasoning_efforts: [] }] });
    if (path.endsWith("/messages")) return json({ items: submitted ? failed ? [user] : [user, assistant] : [] });
    if (path.endsWith("/turns")) return json({ items: submitted ? [{ turn_id: TURN_ID, conversation_id: CONVERSATION_ID, user_message_id: user.message_id, assistant_message_id: failed ? null : assistant.message_id, status: failed ? "FAILED" : "COMPLETED", version: 1 }] : [] });
    if (path.endsWith("/receipts")) return json({ items: submitted ? [{ receipt_id: "receipt_synthetic", conversation_id: CONVERSATION_ID, message_id: user.message_id, request_id: "request_synthetic", capability: "market_data_get", operation: "quote", source_codes: ["SYNTHETIC"], warning_codes: [], error_codes: [], created_at: "2026-09-15T00:00:01Z" }] : [] });
    if (path.endsWith("/metrics")) return json({ metrics: { conversation_id: CONVERSATION_ID, turn_statuses: {} } });
    return json({ items: [] });
  });
  return posts;
}

test("explicit Research budget stops visibly and restores exact report without resubmission", async ({ page }) => {
  const posts = await mockResearchTurn(page);
  await page.goto("/?agent=open");
  await expect(page.getByLabel("Copilot Mode", { exact: true })).toHaveValue("standard");
  await page.getByLabel("Copilot Mode", { exact: true }).selectOption("research");
  await page.getByText("Research Budget", { exact: true }).click();
  await page.getByLabel("Maximum Seconds", { exact: true }).fill("60");
  await page.getByLabel("Maximum Model Calls", { exact: true }).fill("2");
  await page.getByLabel("Maximum Tool Calls", { exact: true }).fill("3");
  await page.getByLabel("Message Copilot").fill("Research synthetic evidence");
  await page.getByRole("button", { name: "Send Message", exact: true }).click();
  await expect(page.getByText("Model call budget reached", { exact: true })).toBeVisible();
  expect(posts).toHaveLength(1);
  const progress = page.getByRole("region", { name: "Research Progress" });
  await expect(progress.getByText("123", { exact: true })).toBeVisible();
  await expect(progress.getByText("45", { exact: true })).toBeVisible();
  await expect(progress.getByText("Partial", { exact: true })).toBeVisible();
  await expect(progress.getByText("Exact Field Matches", { exact: true })).toBeVisible();
  await expect(progress).toContainText("not the meaning of model explanations");
  await expect(progress.getByText("Blocked Blocks", { exact: true })).toBeVisible();
  await expect(progress.getByRole("list", { name: "Research Focus" }).getByRole("listitem")).toHaveCount(3);
  expect(posts[0].body).toMatchObject({ research_mode: "research", research_max_seconds: 60, research_max_model_calls: 2, research_max_tool_calls: 3 });
  await page.getByRole("link", { name: "request_synthetic/result/last" }).click();
  await expect(page.locator("#copilot-receipt-receipt_synthetic")).toBeVisible();
  await page.reload();
  await expect(page.getByText("Model call budget reached", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Research Progress" })).toContainText("Unavailable");
  expect(posts).toHaveLength(1);
});

test("failed Research reload reconstructs saved steps without inventing usage", async ({ page }) => {
  const posts = await mockResearchTurn(page, true);
  await page.goto("/?agent=open");
  await page.getByLabel("Copilot Mode", { exact: true }).selectOption("research");
  await page.getByLabel("Message Copilot").fill("Research synthetic evidence");
  await page.getByRole("button", { name: "Send Message", exact: true }).click();
  await expect(page.getByText(/Progress and model usage unavailable after recovery/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Progress and model usage unavailable after recovery/)).toBeVisible();
  await expect(page.getByRole("region", { name: "Research Progress" }).getByText("Exact Field Matches", { exact: true })).toHaveCount(0);
  await page.getByText("Saved Tool Steps · 1", { exact: true }).click();
  await expect(page.getByRole("region", { name: "Research Progress" })).toContainText("market_data_get");
  expect(posts).toHaveLength(1);
});

test("Counter-review requires explicit selection and validates budgets before submission", async ({ page }) => {
  const posts = await mockResearchTurn(page);
  await page.goto("/?agent=open");
  await page.getByLabel("Copilot Mode", { exact: true }).selectOption("challenge");
  await page.getByText("Research Budget", { exact: true }).click();
  await page.getByLabel("Maximum Seconds", { exact: true }).fill("29");
  await page.getByLabel("Message Copilot").fill("Research synthetic evidence");
  await page.getByLabel("Message Copilot").press("Enter");
  await expect(page.getByText(/Research budget must use whole numbers/)).toBeVisible();
  expect(posts).toHaveLength(0);
  await page.getByLabel("Maximum Seconds", { exact: true }).fill("60");
  await page.getByLabel("Message Copilot").press("Enter");
  await expect(page.getByText("Model call budget reached", { exact: true })).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0].body.research_mode).toBe("challenge");
});

for (const width of [390, 1440]) {
  test(`Research field presentation preserves scope and exact copy at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1100 });
    const first = "result/quotes/0/last: 100 · instrument_id=equity:US:SYNTH · currency=USD · quote_at=2026-09-15T12:00:00Z · price_basis=last · sources=SYNTHETIC · degraded=false";
    const second = "result/quotes/1/last: 100 · instrument_id=equity:CN:600000 · currency=CNY · quote_at=2026-09-14T03:00:00Z · price_basis=last · freshness=stale · warnings=QUOTE_STALE · sources=SYNTHETIC · degraded=true";
    const content = `## 推断\n\n比较〔${first}〕与〔${second}〕，币种与时点不同，不能合并判断。\n\n\`evidence=req_synthetic/result/quotes/0/last, req_synthetic/result/quotes/1/last\`\n\n## 缺口\n\nresult/fees: null · currency=USD · degraded=false`;
    const posts = await mockResearchTurn(page, false, content);
    await page.goto("/?agent=open");
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
        writeText: async (value: string) => { document.body.dataset.syntheticCopied = value; },
      } });
    });
    await page.getByLabel("Message Copilot").fill("Read synthetic evidence");
    await page.getByRole("button", { name: "Send Message", exact: true }).click();
    const fields = page.getByRole("region", { name: "Research Evidence · Last Price", exact: true });
    await expect(fields).toHaveCount(2);
    await expect(fields.nth(0)).toContainText("equity:US:SYNTH");
    await expect(fields.nth(0)).toContainText("USD");
    await expect(fields.nth(0)).not.toContainText("CNY");
    await expect(fields.nth(1)).toContainText("equity:CN:600000");
    await expect(fields.nth(1)).toContainText("CNY");
    await expect(fields.nth(1)).toContainText("2026-09-14T03:00:00Z");
    await expect(fields.nth(1)).toContainText("QUOTE_STALE");
    await expect(page.getByRole("heading", { name: "推断", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "事实", exact: true })).toHaveCount(0);
    await expect(page.getByText("，币种与时点不同，不能合并判断。", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Research Evidence · Fees", exact: true })).toContainText("Unavailable (null)");
    await expect(fields.nth(0).locator("pre")).not.toBeVisible();
    await fields.nth(0).getByText("View Raw Evidence Field", { exact: true }).click();
    await expect(fields.nth(0).locator("pre")).toHaveText(first);
    await page.getByText("View Answer References", { exact: true }).click();
    await expect(page.getByText("`evidence=req_synthetic/result/quotes/0/last, req_synthetic/result/quotes/1/last`", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Copy Message", exact: true }).last().click();
    await expect.poll(() => page.evaluate(() => document.body.dataset.syntheticCopied)).toBe(content);
    expect(posts).toHaveLength(1);
    const answer = page.locator(".agent-rail-message.assistant").last();
    expect(await answer.evaluate((node) => node.scrollWidth <= node.clientWidth + 2)).toBeTruthy();
    if (width === 1440) {
      await page.getByRole("button", { name: "Expand Copilot Focus View", exact: true }).click();
      await fields.nth(0).scrollIntoViewIfNeeded();
      await page.screenshot({ path: "../artifacts/research-evidence-preview.png" });
    }
  });
}

test("Research malformed field remains original prose", async ({ page }) => {
  const content = "## 推断\n\n原文〔result/last: 100 · unknown=USD〕保留。";
  await mockResearchTurn(page, false, content);
  await page.goto("/?agent=open");
  await page.getByLabel("Message Copilot").fill("Read synthetic malformed evidence");
  await page.getByRole("button", { name: "Send Message", exact: true }).click();
  await expect(page.getByText("原文〔result/last: 100 · unknown=USD〕保留。", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: /Research Evidence/ })).toHaveCount(0);
});
