"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArrowUp,
  ExternalLink,
  MessageSquarePlus,
  RefreshCw,
  Square,
} from "lucide-react";
import {
  AgentConversation,
  AgentMessage,
  AgentPendingAction,
  AgentReceipt,
  AgentStatus,
  AgentStreamEvent,
  archiveAgentConversation,
  createAgentConversation,
  createTelegramHandoff,
  decideAgentPendingAction,
  fetchAgentConversations,
  fetchAgentMessages,
  fetchAgentReceipts,
  fetchAgentStatus,
  parseReceipt,
  parsePendingAction,
  streamAgentMessage,
} from "../lib/agent-api";

type Dict = Record<string, unknown>;

type StreamSnapshot = {
  phase: "waiting" | "tool" | "streaming" | "complete";
  draft: string;
  receipts: AgentReceipt[];
  sourceUrls: string[];
  chartLinks: string[];
  researchSubjectIds: string[];
  pendingSummary: string | null;
  pendingAction: { action: AgentPendingAction; token: string } | null;
  error: string | null;
};

const EMPTY_STREAM: StreamSnapshot = {
  phase: "waiting",
  draft: "",
  receipts: [],
  sourceUrls: [],
  chartLinks: [],
  researchSubjectIds: [],
  pendingSummary: null,
  pendingAction: null,
  error: null,
};

function asRecord(value: unknown): Dict {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Dict)
    : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function displayDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function eventPayload(event: AgentStreamEvent): Dict {
  return asRecord(event.payload);
}

function firstText(source: Dict, keys: string[]): string {
  for (const key of keys) {
    const candidate = text(source[key]);
    if (candidate) return candidate;
  }
  return "";
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function researchSubjectIds(value: unknown): string[] {
  const source = asRecord(value);
  return Array.from(new Set([
    ...stringList(source.research_subject_ids),
    ...stringList(source.subject_ids),
    ...[
      source.research_subject_id,
      source.subject_id,
      source.case_id,
    ].filter((item): item is string => typeof item === "string" && item.trim().length > 0),
  ]));
}

function collectLinks(value: unknown): string[] {
  if (typeof value === "string") {
    return /^(?:https?:\/\/|\/api\/|\/artifacts\/)/i.test(value) ? [value] : [];
  }
  if (Array.isArray(value)) return value.flatMap(collectLinks);
  if (value === null || typeof value !== "object") return [];
  const source = value as Dict;
  return [
    ...collectLinks(source.url),
    ...collectLinks(source.href),
    ...collectLinks(source.artifact_url),
    ...collectLinks(source.display_url),
  ];
}

function initialTitle(conversation: AgentConversation): string {
  return conversation.title || "Untitled conversation";
}

function mergeReceipts(items: AgentReceipt[]): AgentReceipt[] {
  const byId = new Map<string, AgentReceipt>();
  for (const item of items) {
    if (item.receipt_id) byId.set(item.receipt_id, item);
  }
  return Array.from(byId.values()).sort((left, right) =>
    String(right.created_at ?? "").localeCompare(String(left.created_at ?? "")),
  );
}

function statusLabel(status: AgentStatus | null, loading: boolean): string {
  if (loading) return "Checking runtime";
  if (!status) return "Unavailable";
  if (!status.enabled) return "Disabled";
  if (!status.configured) return "Configuration required";
  return "Ready";
}

export function ChatWorkspace() {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [conversationsError, setConversationsError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, AgentMessage[]>>({});
  const [receipts, setReceipts] = useState<Record<string, AgentReceipt[]>>({});
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [streams, setStreams] = useState<Record<string, StreamSnapshot>>({});
  const [composer, setComposer] = useState("");
  const [composerComposing, setComposerComposing] = useState(false);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<{ conversationId: string; token: string; expiresAt: string } | null>(null);
  const controllers = useRef(new Map<string, AbortController>());
  const refreshControllers = useRef(new Map<string, AbortController>());

  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.conversation_id === selectedId) ?? null,
    [conversations, selectedId],
  );
  const selectedMessages = selectedId ? messages[selectedId] ?? [] : [];
  const selectedReceipts = selectedId ? receipts[selectedId] ?? [] : [];
  const selectedStream = selectedId ? streams[selectedId] : undefined;
  const selectedStreaming = selectedId ? controllers.current.has(selectedId) : false;
  const currentReceipts = useMemo(
    () => mergeReceipts([
      ...selectedReceipts,
      ...(selectedStream?.receipts ?? []),
    ]),
    [selectedReceipts, selectedStream?.receipts],
  );
  const relatedSubjectIds = useMemo(
    () => Array.from(new Set([
      ...currentReceipts.flatMap((receipt) => researchSubjectIds(receipt)),
      ...(selectedStream?.researchSubjectIds ?? []),
    ])),
    [currentReceipts, selectedStream?.researchSubjectIds],
  );

  const loadStatus = useCallback(async (signal?: AbortSignal) => {
    setStatusLoading(true);
    setStatusError(null);
    try {
      setStatus(await fetchAgentStatus(signal));
    } catch (error) {
      if (!isAbortError(error)) setStatusError(errorText(error, "Unable to read Agent status"));
    } finally {
      if (!signal?.aborted) setStatusLoading(false);
    }
  }, []);

  const loadConversations = useCallback(async (signal?: AbortSignal) => {
    setConversationsLoading(true);
    setConversationsError(null);
    try {
      const items = await fetchAgentConversations(signal);
      setConversations(items);
      setSelectedId((current) => {
        if (current && items.some((item) => item.conversation_id === current)) return current;
        return items[0]?.conversation_id ?? null;
      });
    } catch (error) {
      if (!isAbortError(error)) setConversationsError(errorText(error, "Unable to load conversations"));
    } finally {
      if (!signal?.aborted) setConversationsLoading(false);
    }
  }, []);

  const reloadDurableConversation = useCallback(async (conversationId: string) => {
    refreshControllers.current.get(conversationId)?.abort();
    const controller = new AbortController();
    refreshControllers.current.set(conversationId, controller);
    if (conversationId === selectedId) {
      setMessagesLoading(true);
      setMessagesError(null);
    }
    try {
      const [nextMessages, nextReceipts] = await Promise.all([
        fetchAgentMessages(conversationId, controller.signal),
        fetchAgentReceipts(conversationId, controller.signal),
      ]);
      setMessages((current) => ({ ...current, [conversationId]: nextMessages }));
      setReceipts((current) => ({ ...current, [conversationId]: nextReceipts }));
    } catch (error) {
      if (!isAbortError(error) && conversationId === selectedId) {
        setMessagesError(errorText(error, "Unable to load durable messages"));
      }
    } finally {
      if (refreshControllers.current.get(conversationId) === controller) {
        refreshControllers.current.delete(conversationId);
      }
      if (conversationId === selectedId && !controller.signal.aborted) setMessagesLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    const statusController = new AbortController();
    const conversationController = new AbortController();
    void loadStatus(statusController.signal);
    void loadConversations(conversationController.signal);
    return () => {
      statusController.abort();
      conversationController.abort();
      refreshControllers.current.forEach((controller) => controller.abort());
      controllers.current.forEach((controller) => controller.abort());
    };
  }, [loadConversations, loadStatus]);

  useEffect(() => {
    if (!selectedId) return;
    void reloadDurableConversation(selectedId);
  }, [reloadDurableConversation, selectedId]);

  function updateStream(conversationId: string, patch: Partial<StreamSnapshot>) {
    setStreams((current) => ({
      ...current,
      [conversationId]: { ...(current[conversationId] ?? EMPTY_STREAM), ...patch },
    }));
  }

  function handleStreamEvent(conversationId: string, event: AgentStreamEvent) {
    const payload = eventPayload(event);
    if (event.jsonError) {
      updateStream(conversationId, { error: "The Agent stream returned an invalid event payload." });
      return;
    }
    switch (event.event) {
      case "message_started":
        updateStream(conversationId, { phase: "waiting", error: null });
        return;
      case "tool_started":
        updateStream(conversationId, { phase: "tool", error: null });
        return;
      case "tool_finished": {
        const receipt = parseReceipt(payload.receipt ?? payload);
        const links = collectLinks(payload);
        if (receipt) {
          setStreams((current) => {
            const existing = current[conversationId] ?? EMPTY_STREAM;
            return {
              ...current,
              [conversationId]: {
                ...existing,
                phase: "tool",
                receipts: mergeReceipts([...existing.receipts, receipt]),
                sourceUrls: Array.from(new Set([...existing.sourceUrls, ...stringList(payload.source_urls), ...links])),
                chartLinks: Array.from(new Set([...existing.chartLinks, ...collectLinks(payload.chart_artifact)])),
                researchSubjectIds: Array.from(new Set([
                  ...existing.researchSubjectIds,
                  ...researchSubjectIds(payload),
                  ...researchSubjectIds(receipt),
                ])),
              },
            };
          });
        }
        return;
      }
      case "text_delta": {
        const delta = firstText(payload, ["text_delta", "delta", "text", "content"])
          || (typeof event.payload === "string" ? event.payload : "");
        if (delta) {
          setStreams((current) => {
            const existing = current[conversationId] ?? EMPTY_STREAM;
            return {
              ...current,
              [conversationId]: {
                ...existing,
                phase: "streaming",
                draft: existing.draft + delta,
                error: null,
              },
            };
          });
        }
        return;
      }
      case "pending_action": {
        const action = parsePendingAction(payload.pending_action ?? payload);
        const token = text(payload.confirmation_token);
        const summary = action?.presented_summary
          || firstText(payload, ["presented_summary", "summary", "message"]);
        updateStream(conversationId, {
          phase: "tool",
          pendingSummary: summary || "This action is disabled in the current read-only milestone.",
          pendingAction: action && token ? { action, token } : null,
        });
        return;
      }
      case "completed": {
        const finalText = firstText(payload, ["text", "content", "answer"])
          || (typeof event.payload === "string" ? event.payload : "");
        setStreams((current) => {
          const existing = current[conversationId] ?? EMPTY_STREAM;
          return {
            ...current,
            [conversationId]: {
              ...existing,
              phase: "complete",
              draft: finalText || existing.draft,
              sourceUrls: Array.from(new Set([
                ...existing.sourceUrls,
                ...stringList(payload.web_source_urls ?? payload.source_urls),
              ])),
              chartLinks: Array.from(new Set([
                ...existing.chartLinks,
                ...collectLinks(payload.chart_artifact),
              ])),
              researchSubjectIds: Array.from(new Set([
                ...existing.researchSubjectIds,
                ...researchSubjectIds(payload),
              ])),
            },
          };
        });
        return;
      }
      case "failed": {
        const message = firstText(payload, ["message", "detail", "error", "code"]);
        updateStream(conversationId, {
          phase: "complete",
          error: message || "The Agent stream failed before completion.",
        });
        return;
      }
      default:
        return;
    }
  }

  async function sendMessage() {
    const content = composer.trim();
    if (!content || actionBusy || disabledReason) return;
    let conversationId = selectedId;
    if (!conversationId) {
      setActionBusy("new");
      setActionError(null);
      try {
        const conversation = await createAgentConversation();
        conversationId = conversation.conversation_id;
        setConversations((current) => [conversation, ...current.filter(
          (item) => item.conversation_id !== conversation.conversation_id,
        )]);
        setSelectedId(conversationId);
      } catch (error) {
        setActionError(errorText(error, "Unable to create a conversation"));
        return;
      } finally {
        setActionBusy(null);
      }
    }
    if (controllers.current.has(conversationId)) return;
    setComposer("");
    setActionError(null);
    const optimistic: AgentMessage = {
      message_id: `local-${Date.now()}`,
      conversation_id: conversationId,
      role: "USER",
      content,
      sequence: null,
      created_at: new Date().toISOString(),
    };
    setMessages((current) => ({
      ...current,
      [conversationId]: [...(current[conversationId] ?? []), optimistic],
    }));
    const controller = new AbortController();
    controllers.current.set(conversationId, controller);
    setStreams((current) => ({
      ...current,
      [conversationId]: { ...EMPTY_STREAM, phase: "waiting" },
    }));
    try {
      await streamAgentMessage(
        conversationId,
        content,
        (event) => handleStreamEvent(conversationId, event),
        controller.signal,
      );
      if (!controller.signal.aborted) {
        await reloadDurableConversation(conversationId);
        await loadConversations();
      }
    } catch (error) {
      if (!isAbortError(error) && !controller.signal.aborted) {
        updateStream(conversationId, {
          phase: "complete",
          error: errorText(error, "The Agent stream could not be started."),
        });
      }
    } finally {
      controllers.current.delete(conversationId);
      if (!controller.signal.aborted) {
        setStreams((current) => {
          const existing = current[conversationId];
          if (!existing) return current;
          return {
            ...current,
            [conversationId]: {
              ...existing,
              draft: "",
              phase: "complete",
            },
          };
        });
      }
    }
  }

  async function decidePendingAction(decision: "confirm" | "reject") {
    if (!selectedId || !selectedStream?.pendingAction || actionBusy) return;
    const pending = selectedStream.pendingAction;
    setActionBusy(`pending:${pending.action.action_id}`);
    setActionError(null);
    try {
      const updated = await decideAgentPendingAction(
        selectedId,
        pending.action,
        pending.token,
        decision,
      );
      setStreams((current) => {
        const existing = current[selectedId] ?? EMPTY_STREAM;
        return {
          ...current,
          [selectedId]: {
            ...existing,
            pendingAction: null,
            pendingSummary: `${updated.capability}.${updated.operation}: ${updated.status}`,
          },
        };
      });
      await reloadDurableConversation(selectedId);
    } catch (error) {
      setActionError(errorText(error, `Unable to ${decision} this pending action`));
    } finally {
      setActionBusy(null);
    }
  }

  function stopWaiting() {
    if (!selectedId) return;
    const controller = controllers.current.get(selectedId);
    if (!controller) return;
    controller.abort();
    controllers.current.delete(selectedId);
    setStreams((current) => {
      const next = { ...current };
      delete next[selectedId];
      return next;
    });
    // Stopping only aborts this browser request.  The durable store remains
    // authoritative, so immediately reread it without issuing a cancellation
    // command to the Agent service.
    void reloadDurableConversation(selectedId);
  }

  async function newConversation() {
    if (actionBusy) return;
    setActionBusy("new");
    setActionError(null);
    try {
      const conversation = await createAgentConversation();
      setConversations((current) => [conversation, ...current.filter(
        (item) => item.conversation_id !== conversation.conversation_id,
      )]);
      setSelectedId(conversation.conversation_id);
    } catch (error) {
      setActionError(errorText(error, "Unable to create a conversation"));
    } finally {
      setActionBusy(null);
    }
  }

  async function archiveSelected() {
    if (!selectedConversation || selectedStreaming || actionBusy) return;
    const conversationId = selectedConversation.conversation_id;
    setActionBusy(`archive:${conversationId}`);
    setActionError(null);
    try {
      const expectedVersion = typeof selectedConversation.version === "number"
        ? selectedConversation.version
        : 1;
      await archiveAgentConversation(conversationId, expectedVersion);
      const remaining = conversations.filter((item) => item.conversation_id !== conversationId);
      setConversations(remaining);
      setSelectedId(remaining[0]?.conversation_id ?? null);
    } catch (error) {
      setActionError(errorText(error, "Unable to archive this conversation"));
    } finally {
      setActionBusy(null);
    }
  }

  async function prepareTelegramHandoff() {
    if (!selectedConversation || selectedStreaming || actionBusy) return;
    setActionBusy("handoff");
    setActionError(null);
    try {
      const value = await createTelegramHandoff(selectedConversation.conversation_id);
      setHandoff({
        conversationId: selectedConversation.conversation_id,
        token: value.token,
        expiresAt: value.expires_at,
      });
    } catch (error) {
      setActionError(errorText(error, "Unable to create a Telegram handoff code"));
    } finally {
      setActionBusy(null);
    }
  }

  function handleComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || composerComposing) return;
    event.preventDefault();
    void sendMessage();
  }

  const disabledReason = !status
    ? statusError || "Waiting for Agent runtime diagnostics."
    : !status.enabled
      ? "Agent runtime is disabled. Enable AGENT_ENABLED to start read-only chat."
      : !status.configured
        ? "Agent endpoint is not configured. Set the provider-neutral LLM_* settings before sending."
        : null;
  const canSend = Boolean(!disabledReason && !selectedStreaming && !actionBusy && composer.trim());
  const statusTone = disabledReason ? "attention" : "ready";

  return (
    <section className="chat-workspace" aria-label="Shared Agent Runtime Chat">
      <div className={`chat-runtime-banner ${statusTone}`}>
        <div className="chat-runtime-state">
          <span className="pulse-dot" aria-hidden="true" />
          <strong>{statusLabel(status, statusLoading)}</strong>
          <span>Confirmation-Gated Agent Runtime</span>
        </div>
        <button className="chat-icon-button" onClick={() => void loadStatus()} type="button" title="Refresh Runtime Status">
          <RefreshCw aria-hidden="true" size={15} />
          <span>Refresh Status</span>
        </button>
      </div>
      {disabledReason && (
        <div className="chat-diagnostic" role="status">
          <strong>Chat Unavailable</strong>
          <span>{disabledReason}</span>
          {status?.diagnostics.map((diagnostic, index) => (
            <small key={`${text(diagnostic.code, "diagnostic")}-${index}`}>
              {text(diagnostic.code, "Diagnostic")}: {text(diagnostic.message ?? diagnostic.detail, "No further detail")}
            </small>
          ))}
        </div>
      )}
      {actionError && <div className="chat-error" role="alert">{actionError}</div>}

      <div className="chat-columns">
        <aside className="chat-panel chat-conversation-panel">
          <header className="chat-panel-heading">
            <div>
              <p className="card-kicker">CONVERSATIONS</p>
              <h2>Agent Memory</h2>
            </div>
            <button className="chat-icon-button" disabled={actionBusy === "new"} onClick={() => void newConversation()} type="button" title="New Conversation">
              <MessageSquarePlus aria-hidden="true" size={16} />
              <span>New</span>
            </button>
          </header>
          <div className="chat-conversation-list">
            {conversationsLoading ? (
              <div className="chat-empty">Loading conversations…</div>
            ) : conversationsError ? (
              <div className="chat-empty chat-empty-error">{conversationsError}</div>
            ) : conversations.length === 0 ? (
              <div className="chat-empty">No durable conversations yet. Start a new read-only session.</div>
            ) : (
              conversations.map((conversation) => {
                const active = conversation.conversation_id === selectedId;
                const running = controllers.current.has(conversation.conversation_id);
                return (
                  <button
                    className={`chat-conversation-row${active ? " active" : ""}`}
                    key={conversation.conversation_id}
                    onClick={() => setSelectedId(conversation.conversation_id)}
                    type="button"
                  >
                    <span className="chat-conversation-row-title">{initialTitle(conversation)}</span>
                    <span className="chat-conversation-row-meta">
                      <span>{running ? "Streaming" : conversation.status}</span>
                      <span>{displayDate(conversation.updated_at)}</span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
          <div className="chat-conversation-footer">
            <button
              className="chat-handoff-button"
              disabled={!selectedConversation || selectedStreaming || actionBusy !== null || Boolean(disabledReason)}
              onClick={() => void prepareTelegramHandoff()}
              type="button"
            >
              Continue in Telegram <span>{actionBusy === "handoff" ? "Preparing…" : "One-Time Code"}</span>
            </button>
            {handoff && handoff.conversationId === selectedConversation?.conversation_id && (
              <div className="chat-handoff-code" role="status">
                <strong>Send this once in the authorized bot:</strong>
                <code>/continue {handoff.token}</code>
                <small>Expires {displayDate(handoff.expiresAt)}. The code is not stored in this browser.</small>
              </div>
            )}
            {selectedConversation && (
              <button className="chat-archive-button" disabled={selectedStreaming || actionBusy !== null} onClick={() => void archiveSelected()} type="button">
                <Archive aria-hidden="true" size={14} />
                {actionBusy?.startsWith("archive:") ? "Archiving…" : "Archive"}
              </button>
            )}
          </div>
        </aside>

        <section className="chat-panel chat-transcript-panel">
          <header className="chat-panel-heading chat-transcript-heading">
            <div>
              <p className="card-kicker">CURRENT THREAD</p>
              <h2>{selectedConversation ? initialTitle(selectedConversation) : "Select a Conversation"}</h2>
            </div>
            <span className="chat-channel-badge">CONSOLE</span>
          </header>
          <div className="chat-message-list" aria-live="polite">
            {!selectedConversation ? (
              <div className="chat-empty">Create or select a conversation to begin.</div>
            ) : messagesLoading && selectedMessages.length === 0 ? (
              <div className="chat-empty">Loading durable messages…</div>
            ) : selectedMessages.length === 0 && !selectedStream ? (
              <div className="chat-empty">Ask about durable research, holdings, watchlists, monitors, or current facts.</div>
            ) : (
              <>
                {selectedMessages.map((message) => (
                  <article className={`chat-message ${message.role === "USER" ? "user" : "assistant"}`} key={message.message_id}>
                    <header><span>{message.role === "USER" ? "You" : "Agent"}</span><time>{displayDate(message.created_at)}</time></header>
                    <pre className="chat-message-body">{message.content}</pre>
                  </article>
                ))}
                {selectedStream?.draft && (
                  <article className="chat-message assistant live">
                    <header><span>Agent · {selectedStream.phase === "tool" ? "using a read tool" : "streaming"}</span><span className="chat-live-dot">LIVE</span></header>
                    <pre className="chat-message-body">{selectedStream.draft}</pre>
                  </article>
                )}
                {selectedStream?.pendingAction ? (
                  <div className="chat-readonly-note">
                    <strong>Explicit Confirmation Required</strong>
                    <span>{selectedStream.pendingAction.action.presented_summary}</span>
                    <span>{selectedStream.pendingAction.action.capability}.{selectedStream.pendingAction.action.operation}</span>
                    {!!selectedStream.pendingAction.action.confirmation_details.length && (
                      <dl className="agent-rail-confirmation-details">
                        {selectedStream.pendingAction.action.confirmation_details.map((detail) => (
                          <div key={`${detail.path}:${detail.value}`}><dt>{detail.path}</dt><dd>{detail.value}</dd></div>
                        ))}
                      </dl>
                    )}
                    <span>Arguments: {selectedStream.pendingAction.action.arguments_sha256}</span>
                    <span>Expires: {displayDate(selectedStream.pendingAction.action.expires_at)}</span>
                    <div className="chat-pending-actions">
                      <button
                        className="chat-send-button"
                        disabled={actionBusy !== null}
                        onClick={() => void decidePendingAction("confirm")}
                        type="button"
                      >Confirm Exact Action</button>
                      <button
                        className="chat-archive-button"
                        disabled={actionBusy !== null}
                        onClick={() => void decidePendingAction("reject")}
                        type="button"
                      >Reject</button>
                    </div>
                  </div>
                ) : selectedStream?.pendingSummary ? (
                  <div className="chat-readonly-note"><strong>Action Status</strong><span>{selectedStream.pendingSummary}</span></div>
                ) : null}
                {selectedStream?.error && <div className="chat-error" role="alert">{selectedStream.error}</div>}
              </>
            )}
          </div>
          {messagesError && <div className="chat-inline-note">{messagesError}</div>}
          <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
            <textarea
              aria-label="Message Agent"
              disabled={Boolean(disabledReason) || selectedStreaming || actionBusy !== null}
              onChange={(event) => setComposer(event.target.value)}
              onCompositionEnd={() => setComposerComposing(false)}
              onCompositionStart={() => setComposerComposing(true)}
              onKeyDown={handleComposerKeyDown}
              placeholder={disabledReason ?? "Ask a durable, read-only investment question…"}
              rows={3}
              value={composer}
            />
            <div className="chat-composer-footer">
              <span>Enter to send · Shift+Enter for a new line</span>
              {selectedStreaming ? (
                <button className="chat-stop-button" onClick={stopWaiting} type="button">
                  <Square aria-hidden="true" size={13} /> Stop Waiting
                </button>
              ) : (
                <button className="chat-send-button" disabled={!canSend} type="submit">
                  Send <ArrowUp aria-hidden="true" size={14} />
                </button>
              )}
            </div>
          </form>
        </section>

        <aside className="chat-panel chat-receipt-panel">
          <header className="chat-panel-heading">
            <div>
              <p className="card-kicker">AUDIT TRAIL</p>
              <h2>Tool Receipts</h2>
            </div>
            <span className="chat-receipt-count">{currentReceipts.length}</span>
          </header>
          {currentReceipts.length === 0 ? (
            <div className="chat-empty">Receipts from this thread will appear here after a read tool runs.</div>
          ) : (
            <div className="chat-receipt-list">
              {currentReceipts.map((receipt) => (
                <article className="chat-receipt" key={receipt.receipt_id}>
                  <header><strong>{receipt.capability}</strong><time>{displayDate(receipt.created_at)}</time></header>
                  <p>{receipt.operation}</p>
                  <dl>
                    <div><dt>Request</dt><dd>{receipt.request_id}</dd></div>
                    {!!receipt.source_codes.length && <div><dt>Source</dt><dd>{receipt.source_codes.join(" · ")}</dd></div>}
                    {!!receipt.warning_codes.length && <div className="warn"><dt>Warnings</dt><dd>{receipt.warning_codes.join(" · ")}</dd></div>}
                    {!!receipt.error_codes.length && <div className="bad"><dt>Errors</dt><dd>{receipt.error_codes.join(" · ")}</dd></div>}
                  </dl>
                </article>
              ))}
            </div>
          )}
          {!!selectedStream?.sourceUrls.length && (
            <div className="chat-source-block">
              <p className="card-kicker">SOURCES</p>
              {Array.from(new Set(selectedStream.sourceUrls)).map((url) => (
                <a href={url} key={url} rel="noreferrer" target="_blank"><ExternalLink aria-hidden="true" size={12} />{url}</a>
              ))}
            </div>
          )}
          {!!selectedStream?.chartLinks.length && (
            <div className="chat-source-block">
              <p className="card-kicker">CHART ARTIFACTS</p>
              {Array.from(new Set(selectedStream.chartLinks)).map((url) => (
                <a href={url} key={url} rel="noreferrer" target="_blank"><ExternalLink aria-hidden="true" size={12} />Open local artifact</a>
              ))}
            </div>
          )}
          {!!relatedSubjectIds.length && (
            <div className="chat-source-block">
              <p className="card-kicker">RELATED RESEARCH</p>
              {relatedSubjectIds.map((subjectId) => (
                <a href={`/research#subject-${encodeURIComponent(subjectId)}`} key={subjectId}>
                  Open Research Subject <code>{subjectId}</code>
                </a>
              ))}
            </div>
          )}
          <p className="chat-receipt-note">Receipts contain source, warning, error, and request identifiers only. Secrets and raw Provider payloads stay server-side.</p>
        </aside>
      </div>
    </section>
  );
}
