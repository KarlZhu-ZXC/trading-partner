"use client";

import { authenticatedFetch } from "./api";
import { getAgentPageContext } from "./agent-page-context";

/**
 * The Agent API is deliberately kept in one module.  The backend is allowed
 * to add an envelope around these records without forcing the Chat workspace
 * to know which transport shape was selected.
 */
export const AGENT_API_ROUTES = {
  status: "/api/agent/status",
  preferences: "/api/agent/preferences",
  resetPreferences: "/api/agent/preferences/reset",
  providerModels: (providerId: string) =>
    `/api/agent/providers/${encodeURIComponent(providerId)}/models`,
  conversations: "/api/agent/conversations",
  conversation: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}`,
  messages: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/messages`,
  receipts: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/receipts`,
  turns: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/turns`,
  metrics: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/metrics`,
  pendingActions: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/pending-actions`,
  stream: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
  cancelTurn: (conversationId: string, turnId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/cancel`,
  turnStream: (conversationId: string, turnId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/stream`,
  retryTurn: (conversationId: string, turnId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/retry`,
  archive: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/archive`,
} as const;

export type AgentRecord = Record<string, unknown>;

export type AgentStatus = AgentRecord & {
  enabled: boolean;
  configured: boolean;
  diagnostics: AgentRecord[];
  default_model_id: string | null;
  providers: AgentModelOption[];
  models: AgentModelOption[];
  components: Record<string, AgentRuntimeComponent>;
};

export type AgentRuntimeComponent = {
  installed: boolean;
  loaded: boolean;
  running: boolean;
  pid: number | null;
  start_time: string | null;
  last_exit: number | null;
  last_error: string | null;
};

export type AgentModelOption = AgentRecord & {
  id: string;
  provider: string;
  model: string;
  api_style: string;
  reasoning_mode: string;
  reasoning_effort: string | null;
  reasoning_efforts: string[];
  native_web_search: string;
  is_default: boolean;
};

export type AgentProviderModelOption = AgentRecord & {
  id: string;
  label: string;
  reasoning_efforts: string[];
  is_default: boolean;
};

export type AgentProviderModelCatalog = AgentRecord & {
  provider_id: string;
  default_model: string | null;
  api_style: string;
  reasoning_mode: string;
  native_web_search: string;
  fetched_at: string | null;
  cached: boolean;
  models: AgentProviderModelOption[];
};

export type AgentConversation = AgentRecord & {
  conversation_id: string;
  title: string;
  status: string;
  updated_at: string | null;
  version: number;
};

export type AgentMessage = AgentRecord & {
  message_id: string;
  conversation_id: string;
  role: string;
  content: string;
  sequence: number | null;
  created_at: string | null;
};

export type AgentReceipt = AgentRecord & {
  receipt_id: string;
  conversation_id: string;
  message_id: string | null;
  capability: string;
  operation: string;
  request_id: string;
  created_at: string | null;
  source_codes: string[];
  warning_codes: string[];
  error_codes: string[];
};

export type AgentTurn = AgentRecord & {
  turn_id: string;
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string | null;
  status: string;
  error_code: string | null;
  started_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  version: number;
};

export type AgentPreferences = AgentRecord & {
  preferences_id: string | null;
  language: "zh-CN" | "en";
  response_density: "compact" | "standard" | "detailed";
  preferred_source_codes: string[];
  risk_style: "balanced" | "cautious" | "direct";
  default_chart: boolean;
  web_background: boolean;
  version: number;
  updated_at: string | null;
};

export type AgentConversationMetrics = AgentRecord & {
  conversation_id: string;
  model_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  web_search_calls: number;
  web_extractor_calls: number;
  latency_ms: number;
  turn_statuses: Record<string, number>;
  api_styles: string[];
  truncated: boolean;
};

export type AgentPendingAction = AgentRecord & {
  action_id: string;
  conversation_id: string;
  capability: string;
  operation: string;
  arguments_sha256: string;
  presented_summary: string;
  confirmation_details: Array<{ path: string; value: string }>;
  status: string;
  version: number;
  expires_at: string | null;
};

export type AgentTelegramHandoff = {
  handoff_id: string;
  token: string;
  expires_at: string;
};

export type AgentStreamEvent = {
  event: string;
  id?: string;
  data: string;
  payload: unknown;
  jsonError?: string;
};

/**
 * Small, non-durable context hints sent with a Console message.  These values
 * are intentionally bounded by the caller before they cross the stream
 * boundary and are never treated as a source of truth by the Agent runtime.
 */
export type AgentEphemeralContext = {
  location?: string;
  selection?: string;
  content_excerpt?: string;
  route_hash?: string;
  surface?: string;
  selected_subject_id?: string;
  selected_monitor_id?: string;
  selected_run_id?: string;
  active_tab?: string;
  workbench_subject_id?: string;
};

const EPHEMERAL_LOCATION_LIMIT = 256;
const EPHEMERAL_SELECTION_LIMIT = 1_200;

function boundedText(value: string, limit: number): string {
  return value.replace(/\s+/g, " ").trim().slice(0, limit);
}

function routeHash(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `route:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function safeNavigationField(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const bounded = value.trim().slice(0, 160);
  return /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(bounded) ? bounded : undefined;
}

/**
 * Read only the page location and text the user has explicitly selected. The
 * rail must not silently transmit visible holdings, account, or research data
 * to the configured model; current facts remain available through gated tools.
 */
export function collectEphemeralContext(): AgentEphemeralContext {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return {};
  }

  const location = boundedText(
    `${window.location.pathname}${window.location.search}${window.location.hash}`,
    EPHEMERAL_LOCATION_LIMIT,
  );
  const selection = boundedText(window.getSelection?.()?.toString() ?? "", EPHEMERAL_SELECTION_LIMIT);
  const pageContext = getAgentPageContext();
  return {
    ...(location ? { location } : {}),
    ...(selection ? { selection } : {}),
    ...(location ? { route_hash: routeHash(location) } : {}),
    ...(safeNavigationField(pageContext?.surface)
      ? { surface: safeNavigationField(pageContext?.surface) }
      : {}),
    ...(safeNavigationField(pageContext?.selected_subject_id)
      ? { selected_subject_id: safeNavigationField(pageContext?.selected_subject_id) }
      : {}),
    ...(safeNavigationField(pageContext?.selected_monitor_id)
      ? { selected_monitor_id: safeNavigationField(pageContext?.selected_monitor_id) }
      : {}),
    ...(safeNavigationField(pageContext?.selected_run_id)
      ? { selected_run_id: safeNavigationField(pageContext?.selected_run_id) }
      : {}),
    ...(safeNavigationField(pageContext?.active_tab)
      ? { active_tab: safeNavigationField(pageContext?.active_tab) }
      : {}),
    ...(safeNavigationField(pageContext?.workbench_subject_id)
      ? { workbench_subject_id: safeNavigationField(pageContext?.workbench_subject_id) }
      : {}),
  };
}

function asRecord(value: unknown): AgentRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as AgentRecord)
    : {};
}

function unwrap(value: unknown): AgentRecord {
  const source = asRecord(value);
  const data = asRecord(source.data);
  if (Object.keys(data).length > 0) return data;
  const result = asRecord(source.result);
  if (Object.keys(result).length > 0) return result;
  return source;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function firstBoolean(...values: unknown[]): boolean | undefined {
  for (const value of values) {
    if (typeof value === "boolean") return value;
  }
  return undefined;
}

function arrayFrom(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function findArray(source: AgentRecord, keys: string[]): unknown[] {
  for (const key of keys) {
    const candidate = source[key];
    if (Array.isArray(candidate)) return candidate;
  }
  const data = asRecord(source.data);
  for (const key of keys) {
    const candidate = data[key];
    if (Array.isArray(candidate)) return candidate;
  }
  const result = asRecord(source.result);
  for (const key of keys) {
    const candidate = result[key];
    if (Array.isArray(candidate)) return candidate;
  }
  return [];
}

function idFor(value: AgentRecord, ...keys: string[]): string {
  for (const key of keys) {
    const candidate = text(value[key]);
    if (candidate) return candidate;
  }
  return "";
}

function parseConversation(value: unknown): AgentConversation | null {
  const source = asRecord(value);
  const conversationId = idFor(source, "conversation_id", "id");
  if (!conversationId) return null;
  return {
    ...source,
    conversation_id: conversationId,
    title: text(source.title, "Untitled conversation"),
    status: text(source.status, "ACTIVE").toUpperCase(),
    updated_at: typeof source.updated_at === "string" ? source.updated_at : null,
    version: typeof source.version === "number" ? source.version : 1,
  };
}

function parseMessage(value: unknown): AgentMessage | null {
  const source = asRecord(value);
  const messageId = idFor(source, "message_id", "id");
  if (!messageId) return null;
  const rawRole = text(source.role, "ASSISTANT").toUpperCase();
  return {
    ...source,
    message_id: messageId,
    conversation_id: idFor(source, "conversation_id"),
    role: rawRole === "USER" || rawRole === "ASSISTANT" ? rawRole : rawRole,
    content: typeof source.content === "string"
      ? source.content
      : typeof source.text === "string"
        ? source.text
        : "",
    sequence: typeof source.sequence === "number" ? source.sequence : null,
    created_at: typeof source.created_at === "string" ? source.created_at : null,
  };
}

function parseReceipt(value: unknown): AgentReceipt | null {
  const source = asRecord(value);
  const receiptId = idFor(source, "receipt_id", "id");
  if (!receiptId) return null;
  const toCodes = (candidate: unknown): string[] =>
    arrayFrom(candidate).filter((item): item is string => typeof item === "string");
  return {
    ...source,
    receipt_id: receiptId,
    conversation_id: idFor(source, "conversation_id"),
    message_id: idFor(source, "message_id") || null,
    capability: text(source.capability, "Unknown capability"),
    operation: text(source.operation, "—"),
    request_id: text(source.request_id, "unavailable"),
    created_at: typeof source.created_at === "string" ? source.created_at : null,
    source_codes: toCodes(source.source_codes ?? source.sources),
    warning_codes: toCodes(source.warning_codes ?? source.warnings),
    error_codes: toCodes(source.error_codes ?? source.errors),
  };
}

export function parsePendingAction(value: unknown): AgentPendingAction | null {
  const source = asRecord(value);
  const actionId = idFor(source, "action_id", "id");
  if (!actionId) return null;
  const details = Array.isArray(source.confirmation_details)
    ? source.confirmation_details.flatMap((item) => {
        const detail = asRecord(item);
        const path = text(detail.path);
        const detailValue = text(detail.value);
        return path && detailValue ? [{ path, value: detailValue }] : [];
      }).slice(0, 48)
    : [];
  return {
    ...source,
    action_id: actionId,
    conversation_id: idFor(source, "conversation_id"),
    capability: text(source.capability, "Unknown capability"),
    operation: text(source.operation, "—"),
    arguments_sha256: text(source.arguments_sha256),
    presented_summary: text(source.presented_summary, "Confirm this exact action."),
    confirmation_details: details,
    status: text(source.status, "PRESENTED").toUpperCase(),
    version: typeof source.version === "number" ? source.version : 1,
    expires_at: typeof source.expires_at === "string" ? source.expires_at : null,
  };
}

export function parseTurn(value: unknown): AgentTurn | null {
  const source = asRecord(value);
  const turnId = idFor(source, "turn_id", "id");
  if (!turnId) return null;
  return {
    ...source,
    turn_id: turnId,
    conversation_id: idFor(source, "conversation_id"),
    user_message_id: idFor(source, "user_message_id"),
    assistant_message_id: typeof source.assistant_message_id === "string" ? source.assistant_message_id : null,
    status: text(source.status, "FAILED").toUpperCase(),
    error_code: typeof source.error_code === "string" ? source.error_code : null,
    started_at: typeof source.started_at === "string" ? source.started_at : null,
    updated_at: typeof source.updated_at === "string" ? source.updated_at : null,
    completed_at: typeof source.completed_at === "string" ? source.completed_at : null,
    version: typeof source.version === "number" ? source.version : 1,
  };
}

async function responseError(response: Response): Promise<Error> {
  const body = await response.text();
  if (body) {
    try {
      const parsed = asRecord(JSON.parse(body));
      const detail = text(parsed.detail)
        || text(asRecord(parsed.detail).message)
        || text(parsed.message)
        || text(asRecord(parsed.error).message);
      if (detail) return new Error(detail);
    } catch {
      if (body.length < 240) return new Error(body);
    }
  }
  return new Error(`HTTP ${response.status}`);
}

async function getJson(route: string, signal?: AbortSignal): Promise<unknown> {
  const response = await authenticatedFetch(route, { method: "GET", signal });
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return {};
  return response.json() as Promise<unknown>;
}

async function sendJson(route: string, body: unknown, signal?: AbortSignal): Promise<unknown> {
  return sendJsonMethod(route, "POST", body, signal);
}

async function sendJsonMethod(
  route: string,
  method: "POST" | "PUT",
  body: unknown,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await authenticatedFetch(route, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return {};
  return response.json() as Promise<unknown>;
}

function parsePreferences(value: unknown): AgentPreferences {
  const source = asRecord(value);
  const language = source.language === "en" ? "en" : "zh-CN";
  const density = ["compact", "standard", "detailed"].includes(String(source.response_density))
    ? source.response_density as AgentPreferences["response_density"]
    : "standard";
  const riskStyle = ["balanced", "cautious", "direct"].includes(String(source.risk_style))
    ? source.risk_style as AgentPreferences["risk_style"]
    : "balanced";
  return {
    ...source,
    preferences_id: typeof source.preferences_id === "string" ? source.preferences_id : null,
    language,
    response_density: density,
    preferred_source_codes: arrayFrom(source.preferred_source_codes).filter(
      (item): item is string => typeof item === "string",
    ),
    risk_style: riskStyle,
    default_chart: source.default_chart === true,
    web_background: source.web_background !== false,
    version: typeof source.version === "number" ? source.version : 0,
    updated_at: typeof source.updated_at === "string" ? source.updated_at : null,
  };
}

export async function fetchAgentPreferences(signal?: AbortSignal): Promise<AgentPreferences> {
  const raw = await getJson(AGENT_API_ROUTES.preferences, signal);
  const source = unwrap(raw);
  return parsePreferences(source.preferences ?? source);
}

export async function updateAgentPreferences(
  preferences: AgentPreferences,
  signal?: AbortSignal,
): Promise<AgentPreferences> {
  const raw = await sendJsonMethod(AGENT_API_ROUTES.preferences, "PUT", {
    language: preferences.language,
    response_density: preferences.response_density,
    preferred_source_codes: preferences.preferred_source_codes,
    risk_style: preferences.risk_style,
    default_chart: preferences.default_chart,
    expected_version: preferences.version,
    idempotency_key: crypto.randomUUID(),
    authorization_note: "User updated Agent presentation preferences in Console.",
  }, signal);
  const source = unwrap(raw);
  return parsePreferences(source.preferences ?? source);
}

export async function resetAgentPreferences(
  expectedVersion: number,
  signal?: AbortSignal,
): Promise<AgentPreferences> {
  const raw = await sendJson(AGENT_API_ROUTES.resetPreferences, {
    expected_version: expectedVersion,
    idempotency_key: crypto.randomUUID(),
    authorization_note: "User reset Agent presentation preferences in Console.",
  }, signal);
  const source = unwrap(raw);
  return parsePreferences(source.preferences ?? source);
}

export async function fetchAgentStatus(signal?: AbortSignal): Promise<AgentStatus> {
  const raw = await getJson(AGENT_API_ROUTES.status, signal);
  const source = unwrap(raw);
  const nestedAgent = asRecord(source.agent);
  const enabled = firstBoolean(
    source.enabled,
    source.agent_enabled,
    nestedAgent.enabled,
    nestedAgent.agent_enabled,
  ) ?? false;
  const configured = firstBoolean(
    source.configured,
    source.model_configured,
    source.llm_configured,
    nestedAgent.configured,
    nestedAgent.model_configured,
    nestedAgent.llm_configured,
  ) ?? (
    source.available === true
    || nestedAgent.available === true
    || text(source.state).toUpperCase() === "READY"
    || text(nestedAgent.state).toUpperCase() === "READY"
  );
  const diagnostics = findArray(source, ["diagnostics", "issues", "checks"]).map(asRecord);
  const models = findArray(source, ["providers", "models", "model_options"]).flatMap((value) => {
    const model = asRecord(value);
    const id = text(model.id);
    const name = text(model.model);
    if (!id || !name) return [];
    return [{
      ...model,
      id,
      provider: text(model.provider, id),
      model: name,
      api_style: text(model.api_style),
      reasoning_mode: text(model.reasoning_mode, "none"),
      reasoning_effort: typeof model.reasoning_effort === "string" ? model.reasoning_effort : null,
      reasoning_efforts: Array.isArray(model.reasoning_efforts)
        ? model.reasoning_efforts.filter((value): value is string => typeof value === "string")
        : [],
      native_web_search: text(model.native_web_search, "disabled"),
      is_default: model.is_default === true,
    } satisfies AgentModelOption];
  });
  const defaultModelId = text(source.default_model_id)
    || models.find((item) => item.is_default)?.id
    || models[0]?.id
    || null;
  return {
    ...source,
    enabled,
    configured,
    diagnostics,
    default_model_id: defaultModelId,
    providers: models,
    models,
    components: Object.fromEntries(
      Object.entries(asRecord(source.components)).map(([key, raw]) => {
        const component = asRecord(raw);
        return [key, {
          installed: component.installed === true,
          loaded: component.loaded === true,
          running: component.running === true,
          pid: typeof component.pid === "number" ? component.pid : null,
          start_time: typeof component.start_time === "string" ? component.start_time : null,
          last_exit: typeof component.last_exit === "number" ? component.last_exit : null,
          last_error: typeof component.last_error === "string" ? component.last_error : null,
        } satisfies AgentRuntimeComponent];
      }),
    ),
  };
}

export async function fetchAgentProviderModels(
  providerId: string,
  refresh = false,
  signal?: AbortSignal,
): Promise<AgentProviderModelCatalog> {
  const suffix = refresh ? "?refresh=true" : "";
  const raw = await getJson(`${AGENT_API_ROUTES.providerModels(providerId)}${suffix}`, signal);
  const source = unwrap(raw);
  const models = findArray(source, ["models", "items"]).flatMap((value) => {
    const model = asRecord(value);
    const id = text(model.id);
    if (!id) return [];
    return [{
      ...model,
      id,
      label: text(model.label, id),
      reasoning_efforts: Array.isArray(model.reasoning_efforts)
        ? model.reasoning_efforts.filter((effort): effort is string =>
            typeof effort === "string" && ["low", "medium", "high", "max"].includes(effort))
        : [],
      is_default: model.is_default === true,
    } satisfies AgentProviderModelOption];
  });
  if (models.length === 0) throw new Error("The Provider returned no selectable models");
  return {
    ...source,
    provider_id: text(source.provider_id, providerId),
    default_model: text(source.default_model)
      || models.find((item) => item.is_default)?.id
      || models[0].id,
    api_style: text(source.api_style),
    reasoning_mode: text(source.reasoning_mode, "none"),
    native_web_search: text(source.native_web_search, "disabled"),
    fetched_at: typeof source.fetched_at === "string" ? source.fetched_at : null,
    cached: source.cached === true,
    models,
  };
}

export async function fetchAgentConversations(signal?: AbortSignal): Promise<AgentConversation[]> {
  const raw = await getJson(AGENT_API_ROUTES.conversations, signal);
  return findArray(unwrap(raw), ["conversations", "items"])
    .map(parseConversation)
    .filter((item): item is AgentConversation => item !== null);
}

export async function createAgentConversation(
  title = "New conversation",
  signal?: AbortSignal,
): Promise<AgentConversation> {
  const raw = await sendJson(AGENT_API_ROUTES.conversations, { title }, signal);
  const source = unwrap(raw);
  const candidate = parseConversation(source.conversation) ?? parseConversation(source);
  if (!candidate) throw new Error("The Agent API returned no conversation");
  return candidate;
}

export async function archiveAgentConversation(
  conversationId: string,
  expectedVersion = 1,
  signal?: AbortSignal,
): Promise<void> {
  await sendJson(
    AGENT_API_ROUTES.archive(conversationId),
    { expected_version: expectedVersion },
    signal,
  );
}

export async function decideAgentPendingAction(
  conversationId: string,
  action: AgentPendingAction,
  confirmationToken: string,
  decision: "confirm" | "reject",
  signal?: AbortSignal,
): Promise<AgentPendingAction> {
  const route = `${AGENT_API_ROUTES.conversation(conversationId)}/pending-actions/${encodeURIComponent(action.action_id)}/${decision}`;
  const raw = await sendJson(route, {
    confirmation_token: confirmationToken,
    expected_version: action.version,
  }, signal);
  const source = unwrap(raw);
  const parsed = parsePendingAction(source.action ?? source);
  if (!parsed) throw new Error("The Agent API returned no pending action");
  return parsed;
}

export async function fetchAgentPendingActions(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AgentPendingAction[]> {
  const raw = await getJson(AGENT_API_ROUTES.pendingActions(conversationId), signal);
  return findArray(unwrap(raw), ["items", "pending_actions"])
    .map(parsePendingAction)
    .filter((item): item is AgentPendingAction => item !== null);
}

export async function reissueAgentPendingAction(
  conversationId: string,
  action: AgentPendingAction,
  signal?: AbortSignal,
): Promise<{ action: AgentPendingAction; token: string }> {
  const route = `${AGENT_API_ROUTES.pendingActions(conversationId)}/${encodeURIComponent(action.action_id)}/reissue`;
  const raw = await sendJson(route, { expected_version: action.version }, signal);
  const source = unwrap(raw);
  const parsed = parsePendingAction(source.action);
  const token = text(source.confirmation_token);
  if (!parsed || !token) throw new Error("The Agent API returned no confirmation credential");
  return { action: parsed, token };
}

export async function createTelegramHandoff(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AgentTelegramHandoff> {
  const raw = await sendJson(
    `${AGENT_API_ROUTES.conversation(conversationId)}/handoff/telegram`,
    { ttl_seconds: 600 },
    signal,
  );
  const source = unwrap(raw);
  const handoffId = text(source.handoff_id);
  const token = text(source.token);
  const expiresAt = text(source.expires_at);
  if (!handoffId || !token || !expiresAt) {
    throw new Error("The Agent API returned no Telegram handoff code");
  }
  return { handoff_id: handoffId, token, expires_at: expiresAt };
}

export async function fetchAgentMessages(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AgentMessage[]> {
  const raw = await getJson(AGENT_API_ROUTES.messages(conversationId), signal);
  return findArray(unwrap(raw), ["messages", "items"])
    .map(parseMessage)
    .filter((item): item is AgentMessage => item !== null);
}

export async function fetchAgentReceipts(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AgentReceipt[]> {
  const raw = await getJson(AGENT_API_ROUTES.receipts(conversationId), signal);
  return findArray(unwrap(raw), ["receipts", "items"])
    .map(parseReceipt)
    .filter((item): item is AgentReceipt => item !== null);
}

export async function fetchAgentTurns(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AgentTurn[]> {
  const raw = await getJson(AGENT_API_ROUTES.turns(conversationId), signal);
  return findArray(unwrap(raw), ["items", "turns"])
    .map(parseTurn)
    .filter((item): item is AgentTurn => item !== null);
}

export async function fetchAgentConversationMetrics(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AgentConversationMetrics> {
  const raw = await getJson(AGENT_API_ROUTES.metrics(conversationId), signal);
  const source = asRecord(unwrap(raw).metrics ?? unwrap(raw));
  const nonnegative = (value: unknown): number =>
    typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
  return {
    ...source,
    conversation_id: text(source.conversation_id, conversationId),
    model_calls: nonnegative(source.model_calls),
    input_tokens: nonnegative(source.input_tokens),
    output_tokens: nonnegative(source.output_tokens),
    total_tokens: nonnegative(source.total_tokens),
    web_search_calls: nonnegative(source.web_search_calls),
    web_extractor_calls: nonnegative(source.web_extractor_calls),
    latency_ms: nonnegative(source.latency_ms),
    turn_statuses: asRecord(source.turn_statuses) as Record<string, number>,
    api_styles: arrayFrom(source.api_styles).filter((item): item is string => typeof item === "string"),
    truncated: source.truncated === true,
  };
}

export async function streamAgentMessage(
  conversationId: string,
  content: string,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
  ephemeralContext?: AgentEphemeralContext,
  modelId?: string,
  model?: string,
  reasoningEffort?: string,
): Promise<void> {
  const body: Record<string, unknown> = {
    content,
    external_message_ref: crypto.randomUUID(),
  };
  if (ephemeralContext) body.ephemeral_context = ephemeralContext;
  if (modelId) body.model_id = modelId;
  if (model) body.model = model;
  if (reasoningEffort) body.reasoning_effort = reasoningEffort;
  const response = await authenticatedFetch(AGENT_API_ROUTES.stream(conversationId), {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });
  await consumeAgentStream(response, onEvent);
}

async function consumeAgentStream(
  response: Response,
  onEvent: (event: AgentStreamEvent) => void,
): Promise<void> {
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new Error("The Agent stream returned no body");

  // Importing the small parser here keeps the API adapter usable in tests and
  // avoids shipping a second parser implementation in the React workspace.
  const { createSseParser } = await import("./sse.mjs");
  const parser = createSseParser({ onEvent });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) {
        parser.end(decoder.decode());
        break;
      }
      parser.push(decoder.decode(chunk.value, { stream: true }));
    }
  } finally {
    reader.releaseLock();
  }
}

export async function cancelAgentTurn(
  conversationId: string,
  turnId: string,
  signal?: AbortSignal,
): Promise<AgentTurn> {
  const raw = await sendJson(AGENT_API_ROUTES.cancelTurn(conversationId, turnId), {}, signal);
  const source = unwrap(raw);
  const turn = parseTurn(source.turn ?? source);
  if (!turn) throw new Error("The Agent API returned no cancelled turn");
  return turn;
}

export async function reconnectAgentTurnStream(
  conversationId: string,
  turnId: string,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await authenticatedFetch(AGENT_API_ROUTES.turnStream(conversationId, turnId), {
    method: "GET",
    headers: { Accept: "text/event-stream" },
    signal,
  });
  await consumeAgentStream(response, onEvent);
}

export async function retryAgentTurnStream(
  conversationId: string,
  turnId: string,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await authenticatedFetch(AGENT_API_ROUTES.retryTurn(conversationId, turnId), {
    method: "POST",
    headers: { Accept: "text/event-stream" },
    signal,
  });
  await consumeAgentStream(response, onEvent);
}

export { parseConversation, parseMessage, parseReceipt, unwrap };
