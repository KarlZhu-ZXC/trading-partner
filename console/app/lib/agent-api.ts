"use client";

import { authenticatedFetch } from "./api";

/**
 * The Agent API is deliberately kept in one module.  The backend is allowed
 * to add an envelope around these records without forcing the Chat workspace
 * to know which transport shape was selected.
 */
export const AGENT_API_ROUTES = {
  status: "/api/agent/status",
  conversations: "/api/agent/conversations",
  conversation: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}`,
  messages: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/messages`,
  receipts: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/receipts`,
  stream: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
  archive: (conversationId: string) =>
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/archive`,
} as const;

export type AgentRecord = Record<string, unknown>;

export type AgentStatus = AgentRecord & {
  enabled: boolean;
  configured: boolean;
  diagnostics: AgentRecord[];
};

export type AgentConversation = AgentRecord & {
  conversation_id: string;
  title: string;
  status: string;
  updated_at: string | null;
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
  capability: string;
  operation: string;
  request_id: string;
  created_at: string | null;
  source_codes: string[];
  warning_codes: string[];
  error_codes: string[];
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
};

const EPHEMERAL_LOCATION_LIMIT = 256;
const EPHEMERAL_SELECTION_LIMIT = 1_200;

function boundedText(value: string, limit: number): string {
  return value.replace(/\s+/g, " ").trim().slice(0, limit);
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
    `${window.location.pathname}${window.location.search}`,
    EPHEMERAL_LOCATION_LIMIT,
  );
  const selection = boundedText(window.getSelection?.()?.toString() ?? "", EPHEMERAL_SELECTION_LIMIT);
  return {
    ...(location ? { location } : {}),
    ...(selection ? { selection } : {}),
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

async function responseError(response: Response): Promise<Error> {
  const body = await response.text();
  if (body) {
    try {
      const parsed = asRecord(JSON.parse(body));
      const detail = text(parsed.detail) || text(parsed.message) || text(asRecord(parsed.error).message);
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
  const response = await authenticatedFetch(route, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return {};
  return response.json() as Promise<unknown>;
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
  return {
    ...source,
    enabled,
    configured,
    diagnostics,
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

export async function streamAgentMessage(
  conversationId: string,
  content: string,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
  ephemeralContext?: AgentEphemeralContext,
): Promise<void> {
  const body: Record<string, unknown> = {
    content,
    external_message_ref: crypto.randomUUID(),
  };
  if (ephemeralContext) body.ephemeral_context = ephemeralContext;
  const response = await authenticatedFetch(AGENT_API_ROUTES.stream(conversationId), {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });
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

export { parseConversation, parseMessage, parseReceipt, unwrap };
