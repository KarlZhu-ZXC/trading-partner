"use client";

import {
  Archive,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  GripVertical,
  History,
  Maximize2,
  MessageSquarePlus,
  Minimize2,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  RotateCcw,
  Settings2,
  Square,
  Smartphone,
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
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  AgentConversation,
  AgentMessage,
  AgentPendingAction,
  AgentPreferences,
  AgentProviderModelCatalog,
  AgentReceipt,
  AgentStatus,
  AgentStreamEvent,
  AgentTurn,
  AgentConversationMetrics,
  archiveAgentConversation,
  cancelAgentTurn,
  collectEphemeralContext,
  createTelegramHandoff,
  createAgentConversation,
  decideAgentPendingAction,
  fetchAgentConversations,
  fetchAgentConversationMetrics,
  fetchAgentMessages,
  fetchAgentPendingActions,
  fetchAgentProviderModels,
  fetchAgentPreferences,
  fetchAgentReceipts,
  fetchAgentStatus,
  fetchAgentTurns,
  parsePendingAction,
  parseReceipt,
  reissueAgentPendingAction,
  reconnectAgentTurnStream,
  resetAgentPreferences,
  retryAgentTurnStream,
  streamAgentMessage,
  updateAgentPreferences,
} from "../lib/agent-api";
import { AgentMessageContent } from "./agent-message-content";
import {
  AgentArtifactGallery,
  AgentMessageCard,
  AgentReceiptCard,
} from "./agent-message-card";
import {
  AGENT_RAIL_DEFAULT_WIDTH,
  AGENT_RAIL_MAX_VIEWPORT_RATIO,
  AGENT_RAIL_MAX_WIDTH,
  AGENT_RAIL_MIN_WIDTH,
} from "../lib/agent-rail-layout.mjs";
import { asRecord, textStrict as text } from "../lib/coerce";

type Dict = Record<string, unknown>;
const AGENT_PROVIDER_STORAGE_KEY = "trading-partner-agent-provider-id";
const LEGACY_AGENT_PROVIDER_STORAGE_KEY = "trading-partner-agent-model-id";
const AGENT_MODEL_STORAGE_KEY = "trading-partner-agent-model-name";
const AGENT_REASONING_STORAGE_KEY = "trading-partner-agent-reasoning-effort";
const AGENT_RAIL_WIDTH_STORAGE_KEY = "trading-partner-agent-rail-width";

type StreamSnapshot = {
  turnId: string | null;
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
  turnId: null,
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
  overlayViewport: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
};

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
  return new Intl.DateTimeFormat("en-US", {
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

export function AgentRail({ collapsed, overlayViewport, onCollapsedChange }: AgentRailProps) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const [preferences, setPreferences] = useState<AgentPreferences | null>(null);
  const [preferenceDraft, setPreferenceDraft] = useState<AgentPreferences | null>(null);
  const [preferencesLoading, setPreferencesLoading] = useState(false);
  const [metrics, setMetrics] = useState<Record<string, AgentConversationMetrics>>({});
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [conversationsError, setConversationsError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, AgentMessage[]>>({});
  const [receipts, setReceipts] = useState<Record<string, AgentReceipt[]>>({});
  const [pendingActions, setPendingActions] = useState<Record<string, AgentPendingAction[]>>({});
  const [turns, setTurns] = useState<Record<string, AgentTurn[]>>({});
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [streams, setStreams] = useState<Record<string, StreamSnapshot>>({});
  const [composer, setComposer] = useState("");
  const [composerComposing, setComposerComposing] = useState(false);
  const [selectedProviderId, setSelectedProviderId] = useState("");
  const [providerCatalogs, setProviderCatalogs] = useState<Record<string, AgentProviderModelCatalog>>({});
  const [providerModelsLoading, setProviderModelsLoading] = useState(false);
  const [providerModelsError, setProviderModelsError] = useState<string | null>(null);
  const [selectedModelName, setSelectedModelName] = useState("");
  const [selectedReasoningEffort, setSelectedReasoningEffort] = useState("");
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<{ token: string; expiresAt: string } | null>(null);
  const [railWidth, setRailWidth] = useState(AGENT_RAIL_DEFAULT_WIDTH);
  const [focusMode, setFocusMode] = useState(false);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const controllers = useRef(new Map<string, AbortController>());
  const reconnectControllers = useRef(new Map<string, AbortController>());
  const refreshControllers = useRef(new Map<string, AbortController>());
  const modelCatalogController = useRef<AbortController | null>(null);
  const railRef = useRef<HTMLElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const copyResetTimer = useRef<number | null>(null);

  const selectedConversation = useMemo(
    () => conversations.find((item) => item.conversation_id === selectedId) ?? null,
    [conversations, selectedId],
  );
  const selectedMessages = selectedId ? messages[selectedId] ?? [] : [];
  const selectedReceipts = selectedId ? receipts[selectedId] ?? [] : [];
  const selectedPendingActions = selectedId ? pendingActions[selectedId] ?? [] : [];
  const selectedTurns = selectedId ? turns[selectedId] ?? [] : [];
  const selectedMetrics = selectedId ? metrics[selectedId] ?? null : null;
  const selectedStream = selectedId ? streams[selectedId] : undefined;
  const selectedStreaming = selectedId
    ? controllers.current.has(selectedId) || reconnectControllers.current.has(selectedId)
    : false;
  const currentReceipts = useMemo(
    () => mergeReceipts([...selectedReceipts, ...(selectedStream?.receipts ?? [])]),
    [selectedReceipts, selectedStream?.receipts],
  );
  const inlineReceipts = useMemo(() => {
    const userToAssistant = new Map(
      selectedTurns.flatMap((turn) => turn.assistant_message_id
        ? [[turn.user_message_id, turn.assistant_message_id] as const]
        : []),
    );
    const grouped = new Map<string, AgentReceipt[]>();
    for (const receipt of currentReceipts) {
      const messageId = receipt.message_id ?? "";
      const targetId = userToAssistant.get(messageId) ?? messageId;
      if (!targetId) continue;
      grouped.set(targetId, [...(grouped.get(targetId) ?? []), receipt]);
    }
    return grouped;
  }, [currentReceipts, selectedTurns]);
  const unlinkedReceipts = useMemo(() => {
    const linked = new Set(Array.from(inlineReceipts.values()).flatMap((items) =>
      items.map((item) => item.receipt_id)));
    return currentReceipts.filter((receipt) => !linked.has(receipt.receipt_id));
  }, [currentReceipts, inlineReceipts]);
  const configuredProviderOptions = useMemo(
    () => status?.providers ?? status?.models ?? [],
    [status],
  );
  const providerOptions = useMemo(() => configuredProviderOptions.length ? [{
    id: "auto",
    provider: "Auto",
    model: "",
    api_style: "auto",
    reasoning_mode: "none",
    reasoning_effort: null,
    reasoning_efforts: [],
    native_web_search: "provider_default",
    is_default: false,
  }, ...configuredProviderOptions] : [], [configuredProviderOptions]);
  const runtimeComponents = useMemo(
    () => Object.entries(status?.components ?? {}),
    [status?.components],
  );
  const selectedProvider = useMemo(
    () => providerOptions.find((item) => item.id === selectedProviderId) ?? null,
    [providerOptions, selectedProviderId],
  );
  const selectedCatalog = selectedProviderId ? providerCatalogs[selectedProviderId] : undefined;
  const providerModelOptions = useMemo(
    () => selectedCatalog?.models ?? (selectedProvider ? [{
      id: selectedProvider.model,
      label: selectedProvider.model,
      reasoning_efforts: selectedProvider.reasoning_efforts,
      is_default: true,
    }] : []),
    [selectedCatalog, selectedProvider],
  );
  const selectedProviderModel = useMemo(
    () => providerModelOptions.find((item) => item.id === selectedModelName) ?? null,
    [providerModelOptions, selectedModelName],
  );
  const reasoningOptions = useMemo(
    () => selectedProviderModel?.reasoning_efforts ?? selectedProvider?.reasoning_efforts ?? [],
    [selectedProvider, selectedProviderModel],
  );
  const latestTurn = selectedTurns[0] ?? null;
  const durableTurnActive = latestTurn?.status === "RUNNING" || latestTurn?.status === "WAITING_TOOL";
  const durablePendingAction = selectedPendingActions.find((item) => item.status === "PRESENTED") ?? null;
  const streamPendingAction = selectedStream?.pendingAction ?? null;
  const actionablePending = streamPendingAction?.action ?? durablePendingAction;
  const actionableToken = streamPendingAction
    && streamPendingAction.action.action_id === actionablePending?.action_id
    ? streamPendingAction.token
    : null;

  const maximumRailWidth = useCallback(() => {
    if (typeof window === "undefined") return AGENT_RAIL_MAX_WIDTH;
    return Math.max(
      AGENT_RAIL_MIN_WIDTH,
      Math.min(AGENT_RAIL_MAX_WIDTH, Math.floor(window.innerWidth * AGENT_RAIL_MAX_VIEWPORT_RATIO)),
    );
  }, []);

  // Tracks the applied width for the window-resize listener without putting
  // railWidth itself into that effect's dependencies, which would re-register
  // the listener on every pixel of a drag.
  const appliedRailWidthRef = useRef(AGENT_RAIL_DEFAULT_WIDTH);
  const applyRailWidth = useCallback((value: number, persist = false) => {
    const next = Math.max(AGENT_RAIL_MIN_WIDTH, Math.min(maximumRailWidth(), Math.round(value)));
    appliedRailWidthRef.current = next;
    setRailWidth(next);
    document.documentElement.style.setProperty("--agent-rail-user-width", `${next}px`);
    if (persist) {
      try {
        window.localStorage.setItem(AGENT_RAIL_WIDTH_STORAGE_KEY, String(next));
      } catch {
        // The current-session width remains usable when storage is unavailable.
      }
    }
  }, [maximumRailWidth]);

  useEffect(() => {
    let stored = AGENT_RAIL_DEFAULT_WIDTH;
    try {
      const parsed = Number(window.localStorage.getItem(AGENT_RAIL_WIDTH_STORAGE_KEY));
      if (Number.isFinite(parsed)) stored = parsed;
    } catch {
      // Keep the default width.
    }
    applyRailWidth(stored);
    const handleResize = () => applyRailWidth(appliedRailWidthRef.current);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [applyRailWidth]);

  useEffect(() => {
    document.documentElement.classList.toggle("agent-focus-mode", focusMode && !overlayViewport);
    return () => document.documentElement.classList.remove("agent-focus-mode");
  }, [focusMode, overlayViewport]);

  useEffect(() => {
    if (!overlayViewport || collapsed || !railRef.current) return;
    const rail = railRef.current;
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const focusable = () => Array.from(rail.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((item) => item.offsetParent !== null);
    window.requestAnimationFrame(() => (composerRef.current ?? focusable()[0])?.focus());
    const trapFocus = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    rail.addEventListener("keydown", trapFocus);
    return () => {
      rail.removeEventListener("keydown", trapFocus);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [collapsed, overlayViewport]);

  useEffect(() => () => {
    if (copyResetTimer.current !== null) window.clearTimeout(copyResetTimer.current);
  }, []);

  useEffect(() => {
    if (providerOptions.length === 0) {
      setSelectedProviderId("");
      return;
    }
    let stored = "";
    try {
      stored = window.localStorage.getItem(AGENT_PROVIDER_STORAGE_KEY)
        ?? window.localStorage.getItem(LEGACY_AGENT_PROVIDER_STORAGE_KEY)
        ?? "";
    } catch {
      // Private-mode storage failures fall back to the default provider.
    }
    const next = providerOptions.some((item) => item.id === stored)
      ? stored
      : status?.default_model_id ?? providerOptions[0].id;
    setSelectedProviderId(next);
  }, [providerOptions, status?.default_model_id]);

  useEffect(() => {
    modelCatalogController.current?.abort();
    if (selectedProviderId === "auto") {
      setSelectedModelName("");
      setSelectedReasoningEffort("");
      setProviderModelsLoading(false);
      setProviderModelsError(null);
      return;
    }
    if (!selectedProviderId || !selectedProvider) {
      setSelectedModelName("");
      setProviderModelsError(null);
      return;
    }
    const controller = new AbortController();
    modelCatalogController.current = controller;
    setProviderModelsLoading(true);
    setProviderModelsError(null);
    void fetchAgentProviderModels(selectedProviderId, false, controller.signal)
      .then((catalog) => {
        setProviderCatalogs((current) => ({ ...current, [selectedProviderId]: catalog }));
        const stored = window.localStorage.getItem(AGENT_MODEL_STORAGE_KEY) ?? "";
        const next = catalog.models.some((item) => item.id === stored)
          ? stored
          : catalog.default_model ?? catalog.models[0].id;
        setSelectedModelName(next);
        window.localStorage.setItem(AGENT_MODEL_STORAGE_KEY, next);
      })
      .catch((error) => {
        if (isAbortError(error)) return;
        setProviderModelsError("Live model list unavailable; using the configured default.");
        setSelectedModelName(selectedProvider.model);
      })
      .finally(() => {
        if (!controller.signal.aborted) setProviderModelsLoading(false);
      });
    return () => controller.abort();
  }, [selectedProvider, selectedProviderId]);

  useEffect(() => {
    const stored = window.localStorage.getItem(AGENT_REASONING_STORAGE_KEY) ?? "";
    setSelectedReasoningEffort(reasoningOptions.includes(stored) ? stored : "");
  }, [selectedModelName, reasoningOptions]);

  function selectProvider(providerId: string) {
    if (!providerOptions.some((item) => item.id === providerId)) return;
    setSelectedProviderId(providerId);
    setSelectedModelName("");
    window.localStorage.setItem(AGENT_PROVIDER_STORAGE_KEY, providerId);
  }

  function selectModel(modelName: string) {
    if (!providerModelOptions.some((item) => item.id === modelName)) return;
    setSelectedModelName(modelName);
    window.localStorage.setItem(AGENT_MODEL_STORAGE_KEY, modelName);
  }

  function selectReasoningEffort(effort: string) {
    if (effort && !reasoningOptions.includes(effort)) return;
    setSelectedReasoningEffort(effort);
    window.localStorage.setItem(AGENT_REASONING_STORAGE_KEY, effort);
  }

  function beginRailResize(event: ReactPointerEvent<HTMLButtonElement>) {
    if (overlayViewport) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = railWidth;
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    document.documentElement.classList.add("agent-rail-resizing");
    const handleMove = (moveEvent: PointerEvent) => {
      applyRailWidth(startWidth + startX - moveEvent.clientX);
    };
    const handleEnd = () => {
      target.releasePointerCapture(event.pointerId);
      document.documentElement.classList.remove("agent-rail-resizing");
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleEnd);
      window.removeEventListener("pointercancel", handleEnd);
      const current = Number.parseInt(
        document.documentElement.style.getPropertyValue("--agent-rail-user-width"),
        10,
      );
      applyRailWidth(Number.isFinite(current) ? current : railWidth, true);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleEnd);
    window.addEventListener("pointercancel", handleEnd);
  }

  function handleRailResizeKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (overlayViewport) return;
    let next: number | null = null;
    if (event.key === "ArrowLeft") next = railWidth + 16;
    if (event.key === "ArrowRight") next = railWidth - 16;
    if (event.key === "Home") next = AGENT_RAIL_MIN_WIDTH;
    if (event.key === "End") next = maximumRailWidth();
    if (next === null) return;
    event.preventDefault();
    applyRailWidth(next, true);
  }

  async function copyMessage(message: AgentMessage) {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessageId(message.message_id);
      if (copyResetTimer.current !== null) window.clearTimeout(copyResetTimer.current);
      copyResetTimer.current = window.setTimeout(() => setCopiedMessageId(null), 1_600);
    } catch (error) {
      setActionError(errorText(error, "Unable to copy this message"));
    }
  }

  function editMessage(message: AgentMessage) {
    setComposer(message.content);
    setEditingMessageId(message.message_id);
    window.requestAnimationFrame(() => {
      composerRef.current?.focus();
      composerRef.current?.setSelectionRange(message.content.length, message.content.length);
    });
  }

  function retryMessage(message: AgentMessage) {
    const responseIndex = selectedMessages.findIndex((item) => item.message_id === message.message_id);
    if (responseIndex < 0) return;
    for (let index = responseIndex - 1; index >= 0; index -= 1) {
      const candidate = selectedMessages[index];
      if (candidate.role === "USER") {
        void sendMessage(candidate.content);
        return;
      }
    }
    setActionError("The original prompt for this response is no longer loaded.");
  }

  async function retryFailedTurn() {
    if (!selectedId || !latestTurn || latestTurn.status !== "FAILED" || actionBusy) return;
    const controller = new AbortController();
    controllers.current.set(selectedId, controller);
    setStreams((current) => ({ ...current, [selectedId]: { ...EMPTY_STREAM } }));
    setActionError(null);
    try {
      await retryAgentTurnStream(
        selectedId,
        latestTurn.turn_id,
        (event) => handleStreamEvent(selectedId, event),
        controller.signal,
      );
      if (!controller.signal.aborted) {
        await reloadDurableConversation(selectedId);
        await loadConversations();
      }
    } catch (error) {
      if (!isAbortError(error)) setActionError(errorText(error, "Unable to retry this turn"));
    } finally {
      controllers.current.delete(selectedId);
    }
  }

  function updatePreferenceDraft<K extends keyof AgentPreferences>(
    key: K,
    value: AgentPreferences[K],
  ) {
    setPreferenceDraft((current) => current ? { ...current, [key]: value } : current);
  }

  async function savePreferences() {
    if (!preferenceDraft || actionBusy) return;
    setActionBusy("preferences");
    setActionError(null);
    try {
      const value = await updateAgentPreferences(preferenceDraft);
      setPreferences(value);
      setPreferenceDraft(value);
      setPreferencesOpen(false);
    } catch (error) {
      setActionError(errorText(error, "Unable to save Agent preferences"));
      await loadPreferences();
    } finally {
      setActionBusy(null);
    }
  }

  async function resetPreferences() {
    if (!preferences || actionBusy) return;
    setActionBusy("preferences");
    setActionError(null);
    try {
      const value = await resetAgentPreferences(preferences.version);
      setPreferences(value);
      setPreferenceDraft(value);
    } catch (error) {
      setActionError(errorText(error, "Unable to reset Agent preferences"));
      await loadPreferences();
    } finally {
      setActionBusy(null);
    }
  }

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

  const loadPreferences = useCallback(async (signal?: AbortSignal) => {
    setPreferencesLoading(true);
    try {
      const value = await fetchAgentPreferences(signal);
      setPreferences(value);
      setPreferenceDraft(value);
    } catch (error) {
      if (!isAbortError(error)) setActionError(errorText(error, "Unable to load Agent preferences"));
    } finally {
      if (!signal?.aborted) setPreferencesLoading(false);
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
      const [nextMessages, nextReceipts, nextPendingActions, nextTurns, nextMetrics] = await Promise.all([
        fetchAgentMessages(conversationId, controller.signal),
        fetchAgentReceipts(conversationId, controller.signal),
        fetchAgentPendingActions(conversationId, controller.signal),
        fetchAgentTurns(conversationId, controller.signal),
        fetchAgentConversationMetrics(conversationId, controller.signal),
      ]);
      setMessages((current) => ({ ...current, [conversationId]: nextMessages }));
      setReceipts((current) => ({ ...current, [conversationId]: nextReceipts }));
      setPendingActions((current) => ({ ...current, [conversationId]: nextPendingActions }));
      setTurns((current) => ({ ...current, [conversationId]: nextTurns }));
      setMetrics((current) => ({ ...current, [conversationId]: nextMetrics }));
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
    const preferencesController = new AbortController();
    void loadStatus(statusController.signal);
    void loadConversations(conversationsController.signal);
    void loadPreferences(preferencesController.signal);
    return () => {
      statusController.abort();
      conversationsController.abort();
      preferencesController.abort();
      modelCatalogController.current?.abort();
      refreshControllers.current.forEach((controller) => controller.abort());
      controllers.current.forEach((controller) => controller.abort());
      reconnectControllers.current.forEach((controller) => controller.abort());
    };
  }, [loadConversations, loadPreferences, loadStatus]);

  useEffect(() => {
    if (!selectedId) return;
    void reloadDurableConversation(selectedId);
  }, [reloadDurableConversation, selectedId]);

  useEffect(() => {
    setHandoff(null);
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !latestTurn || !durableTurnActive || controllers.current.has(selectedId)) return;
    reconnectControllers.current.get(selectedId)?.abort();
    const controller = new AbortController();
    reconnectControllers.current.set(selectedId, controller);
    setStreams((current) => ({
      ...current,
      [selectedId]: {
        ...(current[selectedId] ?? EMPTY_STREAM),
        turnId: latestTurn.turn_id,
        phase: latestTurn.status === "WAITING_TOOL" ? "tool" : "waiting",
      },
    }));
    void reconnectAgentTurnStream(
      selectedId,
      latestTurn.turn_id,
      (event) => handleStreamEvent(selectedId, event),
      controller.signal,
    ).catch((error: unknown) => {
      if (!isAbortError(error)) {
        updateStream(selectedId, { error: errorText(error, "Unable to reconnect to this turn") });
      }
    }).finally(() => {
      if (reconnectControllers.current.get(selectedId) === controller) {
        reconnectControllers.current.delete(selectedId);
      }
      if (!controller.signal.aborted) void reloadDurableConversation(selectedId);
    });
    return () => {
      controller.abort();
      if (reconnectControllers.current.get(selectedId) === controller) {
        reconnectControllers.current.delete(selectedId);
      }
    };
  }, [durableTurnActive, latestTurn?.status, latestTurn?.turn_id, reloadDurableConversation, selectedId]);

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
        updateStream(conversationId, {
          turnId: text(payload.turn_id) || null,
          phase: "waiting",
          error: null,
        });
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
          const artifactUrls = safeLinks(payload.artifact_urls, true);
          return {
            ...current,
            [conversationId]: {
              ...existing,
              phase: "complete",
              draft: finalText || existing.draft,
              sourceUrls: sourceUrls.length ? sourceUrls : existing.sourceUrls,
              artifactUrls: artifactUrls.length ? artifactUrls : existing.artifactUrls,
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
      case "cancelled":
        updateStream(conversationId, {
          phase: "complete",
          error: null,
          pendingSummary: "This turn was cancelled. No new tool call will be started.",
        });
        return;
      default:
        return;
    }
  }

  async function sendMessage(contentOverride?: string) {
    const content = (contentOverride ?? composer).trim();
    if (
      !content
      || actionBusy
      || disabledReason
      || !selectedProviderId
      || (selectedProviderId !== "auto" && !selectedModelName)
    ) return;
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
    if (contentOverride === undefined) setComposer("");
    setEditingMessageId(null);
    setActionError(null);
    const optimistic: AgentMessage = {
      message_id: `local-${crypto.randomUUID()}`,
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
        selectedProviderId || undefined,
        selectedModelName || undefined,
        selectedReasoningEffort || undefined,
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
    if (!selectedId || !actionablePending || !actionableToken || actionBusy) return;
    setActionBusy(`pending:${actionablePending.action_id}`);
    setActionError(null);
    try {
      const updated = await decideAgentPendingAction(
        selectedId,
        actionablePending,
        actionableToken,
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

  async function resumePendingAction() {
    if (!selectedId || !durablePendingAction || actionBusy) return;
    setActionBusy(`reissue:${durablePendingAction.action_id}`);
    setActionError(null);
    try {
      const reissued = await reissueAgentPendingAction(selectedId, durablePendingAction);
      updateStream(selectedId, {
        phase: "complete",
        pendingSummary: reissued.action.presented_summary,
        pendingAction: reissued,
      });
      setPendingActions((current) => ({
        ...current,
        [selectedId]: (current[selectedId] ?? []).map((item) =>
          item.action_id === reissued.action.action_id ? reissued.action : item),
      }));
    } catch (error) {
      setActionError(errorText(error, "Unable to resume this confirmation"));
      await reloadDurableConversation(selectedId);
    } finally {
      setActionBusy(null);
    }
  }

  async function cancelCurrentTurn() {
    if (!selectedId || actionBusy) return;
    const turnId = selectedStream?.turnId ?? latestTurn?.turn_id;
    if (!turnId) {
      setActionError("The durable turn is still starting; try cancel again in a moment.");
      return;
    }
    setActionBusy(`cancel:${turnId}`);
    setActionError(null);
    try {
      const cancelled = await cancelAgentTurn(selectedId, turnId);
      controllers.current.get(selectedId)?.abort();
      reconnectControllers.current.get(selectedId)?.abort();
      controllers.current.delete(selectedId);
      reconnectControllers.current.delete(selectedId);
      setTurns((current) => ({
        ...current,
        [selectedId]: [cancelled, ...(current[selectedId] ?? []).filter(
          (item) => item.turn_id !== cancelled.turn_id,
        )],
      }));
      updateStream(selectedId, {
        phase: "complete",
        draft: "",
        error: null,
        pendingSummary: "This turn was cancelled. No new tool call will be started.",
      });
      await reloadDurableConversation(selectedId);
    } catch (error) {
      setActionError(errorText(error, "Unable to cancel this turn"));
      await reloadDurableConversation(selectedId);
    } finally {
      setActionBusy(null);
    }
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

  async function createHandoff() {
    if (!selectedId || actionBusy) return;
    setActionBusy("handoff");
    setActionError(null);
    try {
      const value = await createTelegramHandoff(selectedId);
      setHandoff({ token: value.token, expiresAt: value.expires_at });
    } catch (error) {
      setActionError(errorText(error, "Unable to create a Telegram handoff"));
    } finally {
      setActionBusy(null);
    }
  }

  async function archiveConversation() {
    if (!selectedConversation || actionBusy) return;
    if (!window.confirm(`Archive “${initialTitle(selectedConversation)}”?\n\nMessages and receipts remain durable and read-only.`)) return;
    setActionBusy("archive");
    setActionError(null);
    try {
      await archiveAgentConversation(selectedConversation.conversation_id, selectedConversation.version);
      setHandoff(null);
      await loadConversations();
    } catch (error) {
      setActionError(errorText(error, "Unable to archive this session"));
      await loadConversations();
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
  const canSend = Boolean(
    !disabledReason
    && !selectedStreaming
    && !durableTurnActive
    && !actionBusy
    && selectedProviderId
    && (selectedProviderId === "auto" || selectedModelName)
    && composer.trim(),
  );
  const statusTone = disabledReason ? "attention" : "ready";

  return (
    <>
      <aside
        aria-label="Agent Rail"
        aria-labelledby="console-agent-heading"
        aria-modal={overlayViewport && !collapsed ? true : undefined}
        className={`agent-rail${collapsed ? " collapsed" : ""}`}
        id="console-agent-panel"
        ref={railRef}
        role={overlayViewport ? "dialog" : "complementary"}
      >
        {!collapsed && !overlayViewport && (
          <button
            aria-label="Resize Agent Panel"
            aria-orientation="vertical"
            aria-valuemax={maximumRailWidth()}
            aria-valuemin={AGENT_RAIL_MIN_WIDTH}
            aria-valuenow={focusMode ? maximumRailWidth() : railWidth}
            className="agent-rail-resize-handle"
            onKeyDown={handleRailResizeKeyDown}
            onPointerDown={beginRailResize}
            role="separator"
            title="Drag to resize · Arrow keys adjust width"
            type="button"
          >
            <GripVertical aria-hidden="true" size={13} />
          </button>
        )}
        {collapsed ? (
          <div className="agent-rail-collapsed-control">
            <button
              aria-label="Open Agent Panel"
              className="agent-rail-icon-button"
              onClick={() => onCollapsedChange(false)}
              title="Open Agent Panel"
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
                <h2 id="console-agent-heading">Agent</h2>
              </div>
              <div className="agent-rail-header-actions">
                <span className={`agent-rail-status ${statusTone}`} aria-live="polite">
                  <span className="pulse-dot" aria-hidden="true" />
                  {statusLabel(status, statusLoading)}
                </span>
                <button
                  aria-label="Agent Preferences"
                  aria-expanded={preferencesOpen}
                  className={`agent-rail-icon-button${preferencesOpen ? " active" : ""}`}
                  disabled={preferencesLoading}
                  onClick={() => { setPreferencesOpen((current) => !current); setHistoryOpen(false); }}
                  title="Presentation Preferences"
                  type="button"
                >
                  <Settings2 aria-hidden="true" size={14} />
                </button>
                <button
                  aria-label={focusMode ? "Exit Agent research mode" : "Expand Agent research mode"}
                  aria-pressed={focusMode}
                  className="agent-rail-icon-button"
                  disabled={overlayViewport}
                  onClick={() => setFocusMode((current) => !current)}
                  title={focusMode ? "Exit research mode" : "Research mode · 46% width"}
                  type="button"
                >
                  {focusMode ? <Minimize2 aria-hidden="true" size={14} /> : <Maximize2 aria-hidden="true" size={14} />}
                </button>
                <button
                  aria-label="Refresh Agent Status"
                  className="agent-rail-icon-button"
                  disabled={statusLoading}
                  onClick={() => void loadStatus()}
                  title="Refresh Status"
                  type="button"
                >
                  <RefreshCw aria-hidden="true" size={14} />
                </button>
                <button
                  aria-label="Collapse Agent Panel"
                  className="agent-rail-icon-button"
                  onClick={() => onCollapsedChange(true)}
                  title="Collapse Agent Panel"
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
                New Session
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
              <button
                className="agent-rail-toolbar-button"
                disabled={!selectedConversation || actionBusy !== null || selectedConversation.status !== "ACTIVE"}
                onClick={() => void createHandoff()}
                title="Continue This Session in Telegram"
                type="button"
              >
                <Smartphone aria-hidden="true" size={13} />
                Telegram
              </button>
              <button
                className="agent-rail-toolbar-button"
                disabled={!selectedConversation || actionBusy !== null}
                onClick={() => void archiveConversation()}
                title="Archive This Session"
                type="button"
              >
                <Archive aria-hidden="true" size={13} />
                Archive
              </button>
            </div>

            <div className="agent-rail-scroll" aria-live="polite">
              {!!runtimeComponents.length && !historyOpen && !preferencesOpen && (
                <details className="agent-component-status">
                  <summary>Runtime Components · {runtimeComponents.filter(([, value]) => value.running).length}/{runtimeComponents.length} supervised</summary>
                  <div>
                    {runtimeComponents.map(([key, value]) => (
                      <span className={value.running ? "running" : "stopped"} key={key}>
                        <strong>{key.replaceAll("_", " ")}</strong>
                        <small>{value.running
                          ? `RUNNING · PID ${value.pid ?? "—"}${value.start_time ? ` · ${displayDate(value.start_time)}` : ""}`
                          : value.installed
                            ? `STOPPED · ${value.last_error ?? `EXIT ${value.last_exit ?? "—"}`}`
                            : "NOT INSTALLED"}</small>
                      </span>
                    ))}
                  </div>
                </details>
              )}
              {handoff && !historyOpen && (
                <div className="agent-rail-handoff">
                  <strong>Continue in Telegram</strong>
                  <p>Send <code>/continue {handoff.token}</code> to the configured Trading Partner bot.</p>
                  <small>One-Time Code · Expires {displayDate(handoff.expiresAt)}</small>
                  <button onClick={() => {
                    navigator.clipboard.writeText(`/continue ${handoff.token}`).catch(() => {
                      // Clipboard access can be denied; the token stays visible above.
                    });
                  }} type="button">Copy Command</button>
                </div>
              )}
              {preferencesOpen && preferenceDraft ? (
                <section className="agent-preferences" aria-label="Agent Presentation Preferences">
                  <header>
                    <div><p className="card-kicker">PRESENTATION ONLY</p><h3>Agent Preferences</h3></div>
                    <small>v{preferenceDraft.version}</small>
                  </header>
                  <p>Controls wording and presentation across Console and Telegram. It cannot store prices, positions, orders, or research state.</p>
                  <div className="agent-preferences-grid">
                    <label><span><b className="required-mark" aria-hidden="true">*</b>Language</span><select required value={preferenceDraft.language} onChange={(event) => updatePreferenceDraft("language", event.target.value as AgentPreferences["language"])}><option value="zh-CN">Simplified Chinese</option><option value="en">English</option></select></label>
                    <label><span><b className="required-mark" aria-hidden="true">*</b>Answer Density</span><select required value={preferenceDraft.response_density} onChange={(event) => updatePreferenceDraft("response_density", event.target.value as AgentPreferences["response_density"])}><option value="compact">Compact</option><option value="standard">Standard</option><option value="detailed">Detailed</option></select></label>
                    <label><span><b className="required-mark" aria-hidden="true">*</b>Risk Wording</span><select required value={preferenceDraft.risk_style} onChange={(event) => updatePreferenceDraft("risk_style", event.target.value as AgentPreferences["risk_style"])}><option value="balanced">Balanced</option><option value="cautious">Cautious</option><option value="direct">Direct</option></select></label>
                    <label className="wide"><span>Preferred Source Codes</span><input onChange={(event) => updatePreferenceDraft("preferred_source_codes", event.target.value.split(",").map((item) => item.trim()).filter(Boolean).slice(0, 32))} placeholder="SEC, FRED, YAHOO" value={preferenceDraft.preferred_source_codes.join(", ")} /></label>
                    <label className="toggle"><input checked={preferenceDraft.default_chart} onChange={(event) => updatePreferenceDraft("default_chart", event.target.checked)} type="checkbox" /><span>Prefer a Chart When Useful</span></label>
                    <div className="agent-preference-default"><span>Web Search Background</span><strong>ON BY DEFAULT</strong></div>
                  </div>
                  <div className="agent-preferences-actions">
                    <button disabled={actionBusy !== null} onClick={() => void resetPreferences()} type="button"><RotateCcw aria-hidden="true" size={12} /> Reset</button>
                    <button className="primary" disabled={actionBusy !== null} onClick={() => void savePreferences()} type="button"><Check aria-hidden="true" size={12} /> Save</button>
                  </div>
                </section>
              ) : historyOpen ? (
                <div className="agent-rail-history" aria-label="Agent Session History">
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
                  {selectedMetrics && (
                    <div className="agent-metrics" aria-label="Conversation Usage">
                      <span><strong>{selectedMetrics.model_calls}</strong> Model Calls</span>
                      <span><strong>{selectedMetrics.total_tokens.toLocaleString("en-US")}</strong> tokens</span>
                      <span><strong>{selectedMetrics.web_search_calls + selectedMetrics.web_extractor_calls}</strong> Web Calls</span>
                      <span><strong>{(selectedMetrics.latency_ms / 1000).toFixed(1)}s</strong> Model Time</span>
                      {selectedMetrics.truncated && <small>Bounded sample</small>}
                    </div>
                  )}
                  {!selectedConversation ? (
                    <div className="agent-rail-empty">Ask a durable research or portfolio question. A session starts on first send.</div>
                  ) : messagesLoading && selectedMessages.length === 0 ? (
                    <div className="agent-rail-empty">Loading messages…</div>
                  ) : selectedMessages.length === 0 && !selectedStream ? (
                    <div className="agent-rail-empty">This session is ready. Ask about research, monitors, or current facts.</div>
                  ) : (
                    <>
                      {selectedMessages.map((message) => (
                        <AgentMessageCard
                          copied={copiedMessageId === message.message_id}
                          disabled={Boolean(disabledReason || selectedStreaming || durableTurnActive || actionBusy)}
                          key={message.message_id}
                          message={message}
                          onCopy={(candidate) => void copyMessage(candidate)}
                          onEdit={editMessage}
                          onRetry={retryMessage}
                          receipts={inlineReceipts.get(message.message_id) ?? []}
                        />
                      ))}
                      {selectedStream?.draft && (
                        <article className="agent-rail-message assistant live">
                          <header><span>Agent · {selectedStream.phase === "tool" ? "tool status" : "streaming"}</span><span className="agent-rail-live">LIVE</span></header>
                          <AgentMessageContent content={selectedStream.draft} />
                          <AgentArtifactGallery urls={selectedStream.artifactUrls} />
                        </article>
                      )}
                      {!!selectedStream?.sourceUrls.length && (
                        <div className="agent-rail-source-block" aria-label="Current Web Sources">
                          <span>Current Sources</span>
                          {selectedStream.sourceUrls.map((url, index) => (
                            <a href={url} key={url} rel="noopener noreferrer" target="_blank">
                              <ExternalLink aria-hidden="true" size={10} /> {index + 1}. {new URL(url).hostname}
                            </a>
                          ))}
                        </div>
                      )}
                    </>
                  )}

                  {selectedStreaming && (
                    <div className="agent-rail-tool-state"><Wrench aria-hidden="true" size={13} /> {selectedStream?.phase === "tool" ? "Tool call in progress…" : "Waiting for Agent…"}</div>
                  )}
                  {!selectedStreaming && durableTurnActive && (
                    <div className="agent-rail-tool-state"><Wrench aria-hidden="true" size={13} /> {latestTurn?.status === "WAITING_TOOL" ? "Tool work continues on the server…" : "This turn continues on the server…"}</div>
                  )}
                  {!durableTurnActive && latestTurn?.status === "FAILED" && latestTurn.error_code && (
                    <div className="agent-rail-error agent-turn-failed" role="status">
                      <span>Last Turn Failed · {latestTurn.error_code}</span>
                      <button
                        disabled={Boolean(disabledReason || selectedStreaming || actionBusy)}
                        onClick={() => void retryFailedTurn()}
                        type="button"
                      >
                        <RefreshCw aria-hidden="true" size={11} /> Retry Turn
                      </button>
                    </div>
                  )}
                  {!!unlinkedReceipts.length && (
                    <div className="agent-rail-receipts">
                      <p className="card-kicker">UNLINKED TOOL RECEIPTS · {unlinkedReceipts.length}</p>
                      {unlinkedReceipts.map((receipt) => <AgentReceiptCard key={receipt.receipt_id} receipt={receipt} />)}
                    </div>
                  )}
                  {actionablePending ? (
                    <div className="agent-rail-pending" role="group" aria-label="Pending Action Confirmation">
                      <strong>Confirm Exact Action</strong>
                      <p>{actionablePending.presented_summary}</p>
                      <small>{actionablePending.capability}.{actionablePending.operation}</small>
                      {!!actionablePending.confirmation_details.length && (
                        <dl className="agent-rail-confirmation-details">
                          {actionablePending.confirmation_details.map((detail) => (
                            <div key={`${detail.path}:${detail.value}`}><dt>{detail.path}</dt><dd>{detail.value}</dd></div>
                          ))}
                        </dl>
                      )}
                      <small>Expires {displayDate(actionablePending.expires_at)}</small>
                      {actionableToken ? (
                        <div className="agent-rail-pending-actions">
                          <button disabled={actionBusy !== null} onClick={() => void decidePendingAction("confirm")} type="button"><Check aria-hidden="true" size={13} /> Confirm</button>
                          <button className="reject" disabled={actionBusy !== null} onClick={() => void decidePendingAction("reject")} type="button"><X aria-hidden="true" size={13} /> Reject</button>
                        </div>
                      ) : (
                        <button className="agent-rail-resume-action" disabled={actionBusy !== null} onClick={() => void resumePendingAction()} type="button">Resume Confirmation</button>
                      )}
                    </div>
                  ) : selectedStream?.pendingSummary ? (
                    <div className="agent-rail-pending status"><strong>Action Status</strong><span>{selectedStream.pendingSummary}</span></div>
                  ) : null}
                  {messagesError && <div className="agent-rail-error" role="alert">{messagesError}</div>}
                  {actionError && <div className="agent-rail-error" role="alert">{actionError}</div>}
                  {statusError && <div className="agent-rail-error" role="status">{statusError}</div>}
                  {selectedStream?.error && <div className="agent-rail-error" role="alert">{selectedStream.error}</div>}
                </>
              )}
            </div>

            <form className="agent-rail-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
              {editingMessageId && (
                <div className="agent-composer-editing" role="status">
                  Editing an earlier prompt. Send creates a new durable turn.
                  <button aria-label="Cancel Editing" onClick={() => { setEditingMessageId(null); setComposer(""); }} type="button">Cancel</button>
                </div>
              )}
              <div className="agent-rail-composer-frame">
                <textarea
                  aria-label="Message Agent"
                  disabled={Boolean(disabledReason) || selectedStreaming || durableTurnActive || actionBusy !== null}
                  onChange={(event) => setComposer(event.target.value)}
                  onCompositionEnd={() => setComposerComposing(false)}
                  onCompositionStart={() => setComposerComposing(true)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder={disabledReason ?? (durableTurnActive ? "Current turn is still running…" : "Ask Agent…")}
                  rows={3}
                  ref={composerRef}
                  value={composer}
                />
                {providerModelsError && (
                  <div className="agent-model-catalog-error" role="status">{providerModelsError}</div>
                )}
                <div className="agent-rail-composer-footer">
                  <div className="agent-composer-options">
                    <label className="agent-model-select agent-provider-select">
                      <span className="sr-only">Agent Provider</span>
                      <select
                        aria-label="Agent Provider"
                        disabled={selectedStreaming || providerOptions.length < 2}
                        onChange={(event) => selectProvider(event.target.value)}
                        value={selectedProviderId}
                      >
                        {providerOptions.map((option) => (
                          <option key={option.id} value={option.id}>{option.provider}</option>
                        ))}
                      </select>
                      <ChevronDown aria-hidden="true" size={13} />
                    </label>
                    <label className="agent-model-select agent-model-name-select">
                      <span className="sr-only">Agent Model</span>
                      <select
                        aria-label="Agent Model"
                        disabled={selectedProviderId === "auto" || selectedStreaming || providerModelsLoading || providerModelOptions.length < 2}
                        onChange={(event) => selectModel(event.target.value)}
                        value={selectedModelName}
                      >
                        {selectedProviderId === "auto" && <option value="">Routed Automatically</option>}
                        {selectedProviderId !== "auto" && providerModelsLoading && providerModelOptions.length === 0 && (
                          <option value="">Loading models…</option>
                        )}
                        {providerModelOptions.map((option) => (
                          <option key={option.id} value={option.id}>{option.label}</option>
                        ))}
                      </select>
                      <ChevronDown aria-hidden="true" size={13} />
                    </label>
                    {selectedProviderId !== "auto" && reasoningOptions.length > 0 && (
                      <label className="agent-model-select agent-reasoning-select">
                        <span className="sr-only">Reasoning Effort</span>
                        <select
                          aria-label="Reasoning Effort"
                          disabled={selectedStreaming}
                          onChange={(event) => selectReasoningEffort(event.target.value)}
                          value={selectedReasoningEffort}
                        >
                          <option value="">Auto</option>
                          {reasoningOptions.map((effort) => (
                            <option key={effort} value={effort}>{effort[0].toUpperCase() + effort.slice(1)}</option>
                          ))}
                        </select>
                        <ChevronDown aria-hidden="true" size={13} />
                      </label>
                    )}
                  </div>
                  {selectedStreaming || durableTurnActive ? (
                    <button
                      aria-label="Cancel Current Agent Turn"
                      className="agent-rail-stop"
                      disabled={actionBusy !== null}
                      onClick={() => void cancelCurrentTurn()}
                      title="Cancel the Durable Server Turn"
                      type="button"
                    >
                      <Square aria-hidden="true" size={12} />
                    </button>
                  ) : (
                    <button aria-label="Send Message" className="agent-rail-send" disabled={!canSend} type="submit"><ArrowUp aria-hidden="true" size={15} /></button>
                  )}
                </div>
              </div>
            </form>
          </>
        )}
      </aside>
      {collapsed && (
        <button
          aria-label="Open Agent Panel"
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
