"use client";

import {
  ArrowUp,
  Check,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  History,
  MessageSquarePlus,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Square,
  Wrench,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import {
  AgentConversation,
  AgentMessage,
  AgentPendingAction,
  AgentReceipt,
  AgentStatus,
  AgentStreamEvent,
  collectEphemeralContext,
  createAgentConversation,
  decideAgentPendingAction,
  fetchAgentConversations,
  fetchAgentMessages,
  fetchAgentReceipts,
  fetchAgentStatus,
  parsePendingAction,
  parseReceipt,
  streamAgentMessage,
} from "../lib/agent-api";

type Dict = Record<string, unknown>;

type StreamSnapshot = {
  phase: "waiting" | "tool" | "streaming" | "complete";
  draft: string;
  receipts: AgentReceipt[];
  pendingSummary: string | null;
  pendingAction: { action: AgentPendingAction; token: string } | null;
  error: string | null;
  sourceUrls: string[];
  artifactUrls: string[];
};

const EMPTY_STREAM: StreamSnapshot = {
  phase: "waiting",
  draft: "",
  receipts: [],
  pendingSummary: null,
  pendingAction: null,
  error: null,
  sourceUrls: [],
  artifactUrls: [],
};

type AgentRailProps = {
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
};

function asRecord(value: unknown): Dict {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Dict)
    : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function firstText(source: Dict, keys: string[]): string {
  for (const key of keys) {
    const value = text(source[key]);
    if (value) return value;
  }
  return "";
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

function mergeReceipts(items: AgentReceipt[]): AgentReceipt[] {
  const byId = new Map<string, AgentReceipt>();
  for (const item of items) {
    if (item.receipt_id) byId.set(item.receipt_id, item);
  }
  return Array.from(byId.values()).sort((left, right) =>
    String(right.created_at ?? "").localeCompare(String(left.created_at ?? "")),
  );
}

function initialTitle(conversation: AgentConversation): string {
  return conversation.title || "Untitled conversation";
}

function statusLabel(status: AgentStatus | null, loading: boolean): string {
  if (loading) return "CHECKING";
  if (!status) return "OFFLINE";
  if (!status.enabled) return "DISABLED";
  if (!status.configured) return "SETUP REQUIRED";
  return "READY";
}

function eventPayload(event: AgentStreamEvent): Dict {
  return asRecord(event.payload);
}

function safeLinks(value: unknown, localOnly = false): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value.filter((item): item is string => {
    if (typeof item !== "string" || item.length > 2_048) return false;
    if (localOnly) return item.startsWith("/api/agent/artifacts/");
    try {
      const parsed = new URL(item);
      return (parsed.protocol === "https:" || parsed.protocol === "http:")
        && Boolean(parsed.hostname)
        && !parsed.username
        && !parsed.password;
    } catch {
      return false;
    }
  }))).slice(0, 20);
}

function messageSourceUrls(message: AgentMessage): string[] {
  return safeLinks(asRecord(message.model_receipt).web_source_urls);
}

export function AgentRail({ collapsed, onCollapsedChange }: AgentRailProps) {
  const [historyOpen, setHistoryOpen] = useState(false);
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
  const controllers = useRef(new Map<string, AbortController>());
  const refreshControllers = useRef(new Map<string, AbortController>());

  const selectedConversation = useMemo(
    () => conversations.find((item) => item.conversation_id === selectedId) ?? null,
    [conversations, selectedId],
  );
  const selectedMessages = selectedId ? messages[selectedId] ?? [] : [];
  const selectedReceipts = selectedId ? receipts[selectedId] ?? [] : [];
  const selectedStream = selectedId ? streams[selectedId] : undefined;
  const selectedStreaming = selectedId ? controllers.current.has(selectedId) : false;
  const currentReceipts = useMemo(
    () => mergeReceipts([...selectedReceipts, ...(selectedStream?.receipts ?? [])]),
    [selectedReceipts, selectedStream?.receipts],
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
      if (!isAbortError(error)) setConversationsError(errorText(error, "Unable to load sessions"));
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
        setMessagesError(errorText(error, "Unable to load session messages"));
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
    const conversationsController = new AbortController();
    void loadStatus(statusController.signal);
    void loadConversations(conversationsController.signal);
    return () => {
      statusController.abort();
      conversationsController.abort();
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
        const artifactUrl = text(payload.artifact_url);
        if (receipt) {
          setStreams((current) => {
            const existing = current[conversationId] ?? EMPTY_STREAM;
            return {
              ...current,
              [conversationId]: {
                ...existing,
                phase: "tool",
                receipts: mergeReceipts([...existing.receipts, receipt]),
                artifactUrls: artifactUrl.startsWith("/api/agent/artifacts/")
                  ? Array.from(new Set([...existing.artifactUrls, artifactUrl])).slice(0, 20)
                  : existing.artifactUrls,
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
        updateStream(conversationId, {
          phase: "tool",
          pendingSummary: action?.presented_summary
            || firstText(payload, ["presented_summary", "summary", "message"])
            || "This action requires an explicit decision.",
          pendingAction: action && token ? { action, token } : null,
        });
        return;
      }
      case "completed": {
        const finalText = firstText(payload, ["text", "content", "answer"])
          || (typeof event.payload === "string" ? event.payload : "");
        setStreams((current) => {
          const existing = current[conversationId] ?? EMPTY_STREAM;
          const sourceUrls = safeLinks(payload.web_source_urls);
          return {
            ...current,
            [conversationId]: {
              ...existing,
              phase: "complete",
              draft: finalText || existing.draft,
              sourceUrls: sourceUrls.length ? sourceUrls : existing.sourceUrls,
            },
          };
        });
        return;
      }
      case "failed":
        updateStream(conversationId, {
          phase: "complete",
          error: firstText(payload, ["message", "detail", "error", "code"])
            || "The Agent stream failed before completion.",
        });
        return;
      default:
        return;
    }
  }

  async function sendMessage() {
    const content = composer.trim();
    if (!content || actionBusy || disabledReason) return;
    const ephemeralContext = collectEphemeralContext();
    let conversationId = selectedId;
    if (!conversationId) {
      setActionBusy("new");
      setActionError(null);
      try {
        const title = content.split("\n", 1)[0].trim().slice(0, 72) || "New conversation";
        const conversation = await createAgentConversation(title);
        conversationId = conversation.conversation_id;
        setConversations((current) => [conversation, ...current.filter(
          (item) => item.conversation_id !== conversation.conversation_id,
        )]);
        setSelectedId(conversationId);
      } catch (error) {
        setActionError(errorText(error, "Unable to create a session"));
        return;
      } finally {
        setActionBusy(null);
      }
    }
    if (!conversationId || controllers.current.has(conversationId)) return;
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
    setStreams((current) => ({ ...current, [conversationId]: { ...EMPTY_STREAM, phase: "waiting" } }));
    try {
      await streamAgentMessage(
        conversationId,
        content,
        (event) => handleStreamEvent(conversationId, event),
        controller.signal,
        ephemeralContext,
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
          return { ...current, [conversationId]: { ...existing, draft: "", phase: "complete" } };
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
      setActionError(errorText(error, `Unable to ${decision} this action`));
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
      setHistoryOpen(false);
    } catch (error) {
      setActionError(errorText(error, "Unable to create a session"));
    } finally {
      setActionBusy(null);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || composerComposing) return;
    event.preventDefault();
    void sendMessage();
  }

  const disabledReason = !status
    ? statusError || "Waiting for Agent runtime diagnostics."
    : !status.enabled
      ? "Agent runtime is disabled."
      : !status.configured
        ? "Agent endpoint is not configured."
        : null;
  const canSend = Boolean(!disabledReason && !selectedStreaming && !actionBusy && composer.trim());
  const statusTone = disabledReason ? "attention" : "ready";

  return (
    <>
      <aside
        aria-label="Agent rail"
        className={`agent-rail${collapsed ? " collapsed" : ""}`}
        id="console-agent-panel"
      >
        {collapsed ? (
          <div className="agent-rail-collapsed-control">
            <button
              aria-label="Open Agent panel"
              className="agent-rail-icon-button"
              onClick={() => onCollapsedChange(false)}
              title="Open Agent panel"
              type="button"
            >
              <PanelRightOpen aria-hidden="true" size={17} />
            </button>
            <span className="agent-rail-collapsed-label">AGENT</span>
          </div>
        ) : (
          <>
            <header className="agent-rail-header">
              <div className="agent-rail-heading">
                <p className="card-kicker">LOCAL RUNTIME</p>
                <h2>Agent</h2>
              </div>
              <div className="agent-rail-header-actions">
                <span className={`agent-rail-status ${statusTone}`} aria-live="polite">
                  <span className="pulse-dot" aria-hidden="true" />
                  {statusLabel(status, statusLoading)}
                </span>
                <button
                  aria-label="Refresh Agent status"
                  className="agent-rail-icon-button"
                  disabled={statusLoading}
                  onClick={() => void loadStatus()}
                  title="Refresh status"
                  type="button"
                >
                  <RefreshCw aria-hidden="true" size={14} />
                </button>
                <button
                  aria-label="Collapse Agent panel"
                  className="agent-rail-icon-button"
                  onClick={() => onCollapsedChange(true)}
                  title="Collapse Agent panel"
                  type="button"
                >
                  <PanelRightClose aria-hidden="true" size={16} />
                </button>
              </div>
            </header>

            <div className="agent-rail-toolbar">
              <button
                className="agent-rail-toolbar-button"
                disabled={actionBusy === "new"}
                onClick={() => void newConversation()}
                type="button"
              >
                <MessageSquarePlus aria-hidden="true" size={14} />
                New session
              </button>
              <button
                aria-expanded={historyOpen}
                className={`agent-rail-toolbar-button${historyOpen ? " active" : ""}`}
                onClick={() => setHistoryOpen((current) => !current)}
                type="button"
              >
                <History aria-hidden="true" size={14} />
                History
                {historyOpen ? <ChevronUp aria-hidden="true" size={12} /> : <ChevronDown aria-hidden="true" size={12} />}
              </button>
            </div>

            <div className="agent-rail-scroll" aria-live="polite">
              {historyOpen ? (
                <div className="agent-rail-history" aria-label="Agent session history">
                  {conversationsLoading ? (
                    <div className="agent-rail-empty">Loading sessions…</div>
                  ) : conversationsError ? (
                    <div className="agent-rail-empty error">{conversationsError}</div>
                  ) : conversations.length === 0 ? (
                    <div className="agent-rail-empty">No sessions yet. Start typing below.</div>
                  ) : conversations.map((conversation) => (
                    <button
                      className={`agent-rail-history-row${conversation.conversation_id === selectedId ? " active" : ""}`}
                      key={conversation.conversation_id}
                      onClick={() => { setSelectedId(conversation.conversation_id); setHistoryOpen(false); }}
                      type="button"
                    >
                      <span>{initialTitle(conversation)}</span>
                      <small>{conversation.status} · {displayDate(conversation.updated_at)}</small>
                    </button>
                  ))}
                </div>
              ) : (
                <>
                  {!selectedConversation ? (
                    <div className="agent-rail-empty">Ask a durable research or portfolio question. A session starts on first send.</div>
                  ) : messagesLoading && selectedMessages.length === 0 ? (
                    <div className="agent-rail-empty">Loading messages…</div>
                  ) : selectedMessages.length === 0 && !selectedStream ? (
                    <div className="agent-rail-empty">This session is ready. Ask about research, monitors, or current facts.</div>
                  ) : (
                    <>
                      {selectedMessages.map((message) => (
                        <article className={`agent-rail-message ${message.role === "USER" ? "user" : "assistant"}`} key={message.message_id}>
                          <header><span>{message.role === "USER" ? "You" : "Agent"}</span><time>{displayDate(message.created_at)}</time></header>
                          <pre>{message.content}</pre>
                          {!!messageSourceUrls(message).length && (
                            <div className="agent-rail-source-block" aria-label="Web sources">
                              <span>Sources</span>
                              {messageSourceUrls(message).map((url, index) => (
                                <a href={url} key={url} rel="noopener noreferrer" target="_blank">
                                  <ExternalLink aria-hidden="true" size={10} /> {index + 1}. {new URL(url).hostname}
                                </a>
                              ))}
                            </div>
                          )}
                        </article>
                      ))}
                      {selectedStream?.draft && (
                        <article className="agent-rail-message assistant live">
                          <header><span>Agent · {selectedStream.phase === "tool" ? "tool status" : "streaming"}</span><span className="agent-rail-live">LIVE</span></header>
                          <pre>{selectedStream.draft}</pre>
                        </article>
                      )}
                      {!!selectedStream?.sourceUrls.length && (
                        <div className="agent-rail-source-block" aria-label="Current web sources">
                          <span>Current sources</span>
                          {selectedStream.sourceUrls.map((url, index) => (
                            <a href={url} key={url} rel="noopener noreferrer" target="_blank">
                              <ExternalLink aria-hidden="true" size={10} /> {index + 1}. {new URL(url).hostname}
                            </a>
                          ))}
                        </div>
                      )}
                      {!!selectedStream?.artifactUrls.length && (
                        <div className="agent-rail-source-block" aria-label="Chart artifacts">
                          <span>Chart artifacts</span>
                          {selectedStream.artifactUrls.map((url) => (
                            <a href={url} key={url} rel="noopener noreferrer" target="_blank">
                              <ExternalLink aria-hidden="true" size={10} /> Open generated chart
                            </a>
                          ))}
                        </div>
                      )}
                    </>
                  )}

                  {selectedStreaming && (
                    <div className="agent-rail-tool-state"><Wrench aria-hidden="true" size={13} /> {selectedStream?.phase === "tool" ? "Tool call in progress…" : "Waiting for Agent…"}</div>
                  )}
                  {!!currentReceipts.length && (
                    <div className="agent-rail-receipts">
                      <p className="card-kicker">TOOL RECEIPTS · {currentReceipts.length}</p>
                      {currentReceipts.map((receipt) => (
                        <article className="agent-rail-receipt" key={receipt.receipt_id}>
                          <header><strong>{receipt.capability}</strong><time>{displayDate(receipt.created_at)}</time></header>
                          <span>{receipt.operation}</span>
                          {!!receipt.source_codes.length && <small>Source · {receipt.source_codes.join(" · ")}</small>}
                          {!!receipt.warning_codes.length && <small className="warn">Warning · {receipt.warning_codes.join(" · ")}</small>}
                          {!!receipt.error_codes.length && <small className="bad">Error · {receipt.error_codes.join(" · ")}</small>}
                        </article>
                      ))}
                    </div>
                  )}
                  {selectedStream?.pendingAction ? (
                    <div className="agent-rail-pending" role="group" aria-label="Pending action confirmation">
                      <strong>Confirm exact action</strong>
                      <p>{selectedStream.pendingAction.action.presented_summary}</p>
                      <small>{selectedStream.pendingAction.action.capability}.{selectedStream.pendingAction.action.operation}</small>
                      {!!selectedStream.pendingAction.action.confirmation_details.length && (
                        <dl className="agent-rail-confirmation-details">
                          {selectedStream.pendingAction.action.confirmation_details.map((detail) => (
                            <div key={`${detail.path}:${detail.value}`}><dt>{detail.path}</dt><dd>{detail.value}</dd></div>
                          ))}
                        </dl>
                      )}
                      <small>Expires {displayDate(selectedStream.pendingAction.action.expires_at)}</small>
                      <div className="agent-rail-pending-actions">
                        <button disabled={actionBusy !== null} onClick={() => void decidePendingAction("confirm")} type="button"><Check aria-hidden="true" size={13} /> Confirm</button>
                        <button className="reject" disabled={actionBusy !== null} onClick={() => void decidePendingAction("reject")} type="button"><X aria-hidden="true" size={13} /> Reject</button>
                      </div>
                    </div>
                  ) : selectedStream?.pendingSummary ? (
                    <div className="agent-rail-pending status"><strong>Action status</strong><span>{selectedStream.pendingSummary}</span></div>
                  ) : null}
                  {messagesError && <div className="agent-rail-error" role="alert">{messagesError}</div>}
                  {actionError && <div className="agent-rail-error" role="alert">{actionError}</div>}
                  {statusError && <div className="agent-rail-error" role="status">{statusError}</div>}
                  {selectedStream?.error && <div className="agent-rail-error" role="alert">{selectedStream.error}</div>}
                </>
              )}
            </div>

            <form className="agent-rail-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
              <textarea
                aria-label="Message Agent"
                disabled={Boolean(disabledReason) || selectedStreaming || actionBusy !== null}
                onChange={(event) => setComposer(event.target.value)}
                onCompositionEnd={() => setComposerComposing(false)}
                onCompositionStart={() => setComposerComposing(true)}
                onKeyDown={handleComposerKeyDown}
                placeholder={disabledReason ?? "Ask Agent…"}
                rows={3}
                value={composer}
              />
              <div className="agent-rail-composer-footer">
                <span>Enter send · Shift+Enter newline</span>
                {selectedStreaming ? (
                  <button className="agent-rail-stop" onClick={stopWaiting} type="button"><Square aria-hidden="true" size={12} /> Stop waiting</button>
                ) : (
                  <button className="agent-rail-send" disabled={!canSend} type="submit">Send <ArrowUp aria-hidden="true" size={13} /></button>
                )}
              </div>
            </form>
          </>
        )}
      </aside>
      {collapsed && (
        <button
          aria-label="Open Agent panel"
          className="agent-rail-mobile-tab"
          onClick={() => onCollapsedChange(false)}
          type="button"
        >
          <PanelRightOpen aria-hidden="true" size={16} />
          <span>Agent</span>
        </button>
      )}
    </>
  );
}
