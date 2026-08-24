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
  await expect(page.getByRole("complementary", { name: "Agent" })).toBeVisible();
  await expect(page.getByText("READY", { exact: true })).toBeVisible();

  const provider = page.getByLabel("Agent Provider");
  const model = page.getByLabel("Agent Model");
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
  await page.getByLabel("Message Agent").fill("test durable reconnect");
  await page.getByRole("button", { name: "Send Message" }).click();
  await expect(page.getByText("Recovered durable answer", { exact: true })).toBeVisible();
  expect(api.reconnectCalls()).toBe(1);
});

test("Provider failures render a structured durable notification", async ({ page }) => {
  const api = await mockConsoleApi(page);
  api.failNext();
  await page.goto("/?agent=open");
  await expect(page.getByText("READY", { exact: true })).toBeVisible();
  await page.getByLabel("Agent Provider").selectOption("opencode_zen");
  await page.getByLabel("Agent Model").selectOption("hy3-free");
  await expect(page.getByLabel("Reasoning Effort")).toHaveCount(0);
  await page.getByLabel("Message Agent").fill("trigger provider failure");
  await page.getByRole("button", { name: "Send Message" }).click();

  const notice = page.getByRole("alert", { name: "Agent Provider Error Notification" });
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
  await expect(page.getByRole("alert", { name: "Agent Provider Error Notification" })).toHaveCount(0);
});
