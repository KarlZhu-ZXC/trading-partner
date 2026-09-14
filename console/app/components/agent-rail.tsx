"use client";
import { ResizeHandle } from "./ui/controls";

import {
  Archive,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  GripVertical,
  History,
  ImagePlus,
  Maximize2,
  MessageSquarePlus,
  Minimize2,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  RotateCcw,
  Settings2,
  Square,
  TriangleAlert,
  Wrench,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  AgentConversation,
  AgentFailureNotice,
  AgentMessage,
  AgentPendingAction,
  AgentPreferences,
  AgentProviderModelCatalog,
  AgentReceipt,
  AgentStatus,
  AgentTurn,
  AgentConversationMetrics,
  AgentImageAttachment,
  AgentImageInput,
  type CopilotResearchConfig,
  researchConfigFromReceipt,
  archiveAgentConversation,
  cancelAgentTurn,
  collectEphemeralContext,
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
  reissueAgentPendingAction,
  resetAgentPreferences,
  updateAgentPreferences,
} from "../lib/agent-api";
import { authenticatedFetch } from "../lib/api";
import { CopilotResearchControls } from "./copilot-research-controls";
import { CopilotResearchProgress, CopilotResearchProfiles } from "./copilot-research-progress";
import researchStyles from "./copilot-research-progress.module.css";
import { AgentMessageContent } from "./agent-message-content";
import {
  AgentArtifactGallery,
  AgentMessageCard,
  AgentReceiptCard,
} from "./agent-message-card";
import { Badge, ConfirmationDialog, Disclosure } from "./ui";
import { Button, IconButton, Input, Select, SelectableRow, Textarea, TextLink } from "./ui/controls";
import {
  AGENT_RAIL_DEFAULT_WIDTH,
  AGENT_RAIL_MAX_VIEWPORT_RATIO,
  AGENT_RAIL_MAX_WIDTH,
  AGENT_RAIL_MIN_WIDTH,
} from "../lib/agent-rail-layout.mjs";
import { mergeAgentReceipts } from "../lib/agent-stream";
import { useAgentConversation } from "../lib/use-agent-conversation";
const AGENT_PROVIDER_STORAGE_KEY = "trading-partner-agent-provider-id";
const LEGACY_AGENT_PROVIDER_STORAGE_KEY = "trading-partner-agent-model-id";
const AGENT_MODEL_STORAGE_KEY = "trading-partner-agent-model-name";
const AGENT_REASONING_STORAGE_KEY = "trading-partner-agent-reasoning-effort";
const AGENT_RAIL_WIDTH_STORAGE_KEY = "trading-partner-agent-rail-width";
const AGENT_DISMISSED_FAILURES_STORAGE_KEY = "trading-partner-agent-dismissed-failures";
const AGENT_IMAGE_MAX_BYTES = 2_000_000;
const AGENT_IMAGE_MAX_COUNT = 4;
const AGENT_IMAGE_MAX_TOTAL_BYTES = 4_000_000;
const AGENT_IMAGE_MEDIA_TYPES = new Set(["image/png", "image/jpeg"]);

type ComposerImage = AgentImageInput & {
  id: string;
  media_type: "image/png" | "image/jpeg";
  byte_size: number;
};

function readImageFile(file: File): Promise<ComposerImage> {
  if (!AGENT_IMAGE_MEDIA_TYPES.has(file.type)) {
    return Promise.reject(new Error("Only PNG and JPEG images are supported."));
  }
  if (file.size < 1 || file.size > AGENT_IMAGE_MAX_BYTES) {
    return Promise.reject(new Error("Each image must be 2 MB or smaller."));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Unable to read this image."));
    reader.onload = () => {
      if (typeof reader.result !== "string") {
        reject(new Error("Unable to read this image."));
        return;
      }
      resolve({
        id: crypto.randomUUID(),
        data_url: reader.result,
        name: file.name || null,
        media_type: file.type as ComposerImage["media_type"],
        byte_size: file.size,
      });
    };
    reader.readAsDataURL(file);
  });
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Unable to read this image."));
    reader.onload = () => {
      if (typeof reader.result === "string") resolve(reader.result);
      else reject(new Error("Unable to read this image."));
    };
    reader.readAsDataURL(blob);
  });
}

async function restoreImageAttachment(attachment: AgentImageAttachment): Promise<ComposerImage> {
  const response = await authenticatedFetch(attachment.url);
  if (!response.ok) throw new Error("Unable to reload the attached image.");
  const blob = await response.blob();
  if (blob.type !== attachment.media_type || blob.size > AGENT_IMAGE_MAX_BYTES) {
    throw new Error("The attached image is no longer available.");
  }
  return {
    id: crypto.randomUUID(),
    data_url: await blobToDataUrl(blob),
    name: attachment.original_name,
    media_type: attachment.media_type,
    byte_size: blob.size,
  };
}

function scopedAgentStorageKey(
  base: string,
  providerId: string,
  modelName?: string,
): string {
  return [base, providerId, modelName].filter(Boolean).join(":");
}

function readAgentStorage(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeAgentStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // The current-session selection remains usable without persistent storage.
  }
}

function readDismissedFailureKeys(): Set<string> {
  try {
    const value = JSON.parse(window.localStorage.getItem(AGENT_DISMISSED_FAILURES_STORAGE_KEY) ?? "[]");
    if (!Array.isArray(value)) return new Set();
    return new Set(value.filter((item): item is string =>
      typeof item === "string" && /^[A-Za-z0-9_.:-]{1,320}$/.test(item)).slice(-50));
  } catch {
    return new Set();
  }
}

type AgentRailProps = {
  collapsed: boolean;
  overlayViewport: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
};

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

function AgentFailureNotification({
  notice,
  disabled,
  onDismiss,
  onRetry,
}: {
  notice: AgentFailureNotice;
  disabled: boolean;
  onDismiss: () => void;
  onRetry: () => void;
}) {
  return (
    <section
      aria-label="Copilot Provider Error Notification"
      className="agent-failure-notification"
      role="alert"
    >
      <header>
        <TriangleAlert aria-hidden="true" size={14} />
        <div><strong>{notice.title}</strong><code>{notice.code}</code></div>
        <IconButton
          aria-label="Dismiss Provider Error Notification"
          className="agent-failure-dismiss"
          onClick={onDismiss}
          title="Dismiss Notification"
          type="button"
        >
          <X aria-hidden="true" size={12} />
        </IconButton>
      </header>
      <p>{notice.explanation}</p>
      <dl>
        {notice.provider_id && <div><dt>Provider</dt><dd>{notice.provider_id}</dd></div>}
        {notice.model && <div><dt>Model</dt><dd>{notice.model}</dd></div>}
        {notice.http_status !== null && (
          <div><dt>HTTP Status</dt><dd>{notice.http_status}</dd></div>
        )}
        {notice.retryable !== null && (
          <div><dt>Retryable</dt><dd>{notice.retryable ? "Yes" : "No"}</dd></div>
        )}
        {notice.attempts !== null && <div><dt>Attempts</dt><dd>{notice.attempts}</dd></div>}
      </dl>
      <footer>
        <span>{notice.next_action}</span>
        <Button className="agent-failure-retry" disabled={disabled} onClick={onRetry} size="sm" type="button">
          <RefreshCw aria-hidden="true" size={11} /> Retry Turn
        </Button>
      </footer>
    </section>
  );
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
  const [composer, setComposer] = useState("");
  const [composerImages, setComposerImages] = useState<ComposerImage[]>([]);
  const [composerComposing, setComposerComposing] = useState(false);
  const [selectedProviderId, setSelectedProviderId] = useState("");
  const [providerCatalogs, setProviderCatalogs] = useState<Record<string, AgentProviderModelCatalog>>({});
  const [providerModelsLoading, setProviderModelsLoading] = useState(false);
  const [providerModelsError, setProviderModelsError] = useState<string | null>(null);
  const [selectedModelName, setSelectedModelName] = useState("");
  const [selectedReasoningEffort, setSelectedReasoningEffort] = useState("");
  const [researchConfig, setResearchConfig] = useState<CopilotResearchConfig>({ research_mode: "standard", research_max_seconds: 180, research_max_model_calls: 8, research_max_tool_calls: 24 });
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [archiveConfirmation, setArchiveConfirmation] = useState(false);
  const [railWidth, setRailWidth] = useState(AGENT_RAIL_DEFAULT_WIDTH);
  const [focusMode, setFocusMode] = useState(false);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [dismissedFailureKeys, setDismissedFailureKeys] = useState<Set<string>>(() => new Set());
  const refreshControllers = useRef(new Map<string, AbortController>());
  const modelCatalogController = useRef<AbortController | null>(null);
  const railRef = useRef<HTMLElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const copyResetTimer = useRef<number | null>(null);
  const {
    streams,
    isStreaming,
    updateStream,
    sendMessage: streamMessage,
    retryTurn: retryStream,
    reconnectTurn,
    abortStream,
    abortReconnect,
  } = useAgentConversation({
    reducer: {
      completedSourceLinks: "safe",
      artifactLinks: "safe",
      pendingActionFallback: "This action requires an explicit decision.",
    },
  });

  useEffect(() => {
    setDismissedFailureKeys(readDismissedFailureKeys());
  }, []);

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
  const selectedStreaming = selectedId ? isStreaming(selectedId) : false;
  const currentReceipts = useMemo(
    () => mergeAgentReceipts([...selectedReceipts, ...(selectedStream?.receipts ?? [])]),
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
  const failureNotice = selectedStream?.failureNotice ?? latestTurn?.failure_notice ?? null;
  const failureNoticeKey = failureNotice
    ? selectedStream?.turnId
      ?? latestTurn?.turn_id
      ?? `${selectedId ?? "none"}:${failureNotice.code}:${failureNotice.provider_id ?? "none"}:${failureNotice.model ?? "none"}`
    : null;
  const visibleFailureNotice = failureNotice
    && failureNoticeKey
    && !dismissedFailureKeys.has(failureNoticeKey)
    ? failureNotice
    : null;
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

  const dismissFailureNotice = useCallback(() => {
    if (!failureNoticeKey) return;
    setDismissedFailureKeys((current) => {
      const next = new Set([...current, failureNoticeKey]);
      try {
        window.localStorage.setItem(
          AGENT_DISMISSED_FAILURES_STORAGE_KEY,
          JSON.stringify(Array.from(next).slice(-50)),
        );
      } catch {
        // The current-tab dismissal remains effective when storage is unavailable.
      }
      return next;
    });
  }, [failureNoticeKey]);

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
      stored = readAgentStorage(AGENT_PROVIDER_STORAGE_KEY)
        || readAgentStorage(LEGACY_AGENT_PROVIDER_STORAGE_KEY);
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
        const stored = readAgentStorage(
          scopedAgentStorageKey(AGENT_MODEL_STORAGE_KEY, selectedProviderId),
        ) || readAgentStorage(AGENT_MODEL_STORAGE_KEY);
        const next = catalog.models.some((item) => item.id === stored)
          ? stored
          : catalog.default_model ?? catalog.models[0]?.id ?? selectedProvider.model;
        setSelectedModelName(next);
        writeAgentStorage(
          scopedAgentStorageKey(AGENT_MODEL_STORAGE_KEY, selectedProviderId),
          next,
        );
      })
      .catch((error) => {
        if (isAbortError(error)) return;
        setProviderModelsError("Live model list unavailable; using the configured default.");
        setProviderCatalogs((current) => {
          const next = { ...current };
          delete next[selectedProviderId];
          return next;
        });
        setSelectedModelName(selectedProvider.model);
      })
      .finally(() => {
        if (!controller.signal.aborted) setProviderModelsLoading(false);
      });
    return () => controller.abort();
  }, [selectedProvider, selectedProviderId]);

  useEffect(() => {
    const stored = readAgentStorage(
      scopedAgentStorageKey(
        AGENT_REASONING_STORAGE_KEY,
        selectedProviderId,
        selectedModelName,
      ),
    ) || readAgentStorage(AGENT_REASONING_STORAGE_KEY);
    setSelectedReasoningEffort(reasoningOptions.includes(stored) ? stored : "");
  }, [selectedModelName, selectedProviderId, reasoningOptions]);

  function selectProvider(providerId: string) {
    if (!providerOptions.some((item) => item.id === providerId)) return;
    setSelectedProviderId(providerId);
    setSelectedModelName("");
    writeAgentStorage(AGENT_PROVIDER_STORAGE_KEY, providerId);
  }

  function selectModel(modelName: string) {
    if (!providerModelOptions.some((item) => item.id === modelName)) return;
    setSelectedModelName(modelName);
    writeAgentStorage(
      scopedAgentStorageKey(AGENT_MODEL_STORAGE_KEY, selectedProviderId),
      modelName,
    );
  }

  function selectReasoningEffort(effort: string) {
    if (effort && !reasoningOptions.includes(effort)) return;
    setSelectedReasoningEffort(effort);
    writeAgentStorage(
      scopedAgentStorageKey(
        AGENT_REASONING_STORAGE_KEY,
        selectedProviderId,
        selectedModelName,
      ),
      effort,
    );
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

  async function editMessage(message: AgentMessage) {
    setComposer(message.content);
    setComposerImages([]);
    setEditingMessageId(message.message_id);
    if (message.attachments.length > 0) {
      setActionBusy("images");
      try {
        setComposerImages(await Promise.all(message.attachments.map(restoreImageAttachment)));
      } catch (error) {
        setActionError(errorText(error, "Unable to reload the attached image."));
      } finally {
        setActionBusy(null);
      }
    }
    window.requestAnimationFrame(() => {
      composerRef.current?.focus();
      composerRef.current?.setSelectionRange(message.content.length, message.content.length);
    });
  }

  async function addComposerImages(files: File[]) {
    if (files.length === 0) return;
    setActionError(null);
    const remainingSlots = AGENT_IMAGE_MAX_COUNT - composerImages.length;
    if (files.length > remainingSlots) {
      setActionError(`You can attach up to ${AGENT_IMAGE_MAX_COUNT} images.`);
      return;
    }
    const remainingBytes = AGENT_IMAGE_MAX_TOTAL_BYTES
      - composerImages.reduce((total, image) => total + image.byte_size, 0);
    const added: ComposerImage[] = [];
    let usedBytes = 0;
    try {
      for (const file of files) {
        const image = await readImageFile(file);
        usedBytes += image.byte_size;
        if (usedBytes > remainingBytes) {
          throw new Error("Attached images must total 4 MB or less.");
        }
        added.push(image);
      }
      setComposerImages((current) => [...current, ...added]);
    } catch (error) {
      setActionError(errorText(error, "Unable to attach this image"));
    }
  }

  function handleImageInput(event: ChangeEvent<HTMLInputElement>) {
    void addComposerImages(Array.from(event.target.files ?? []));
    event.target.value = "";
  }

  function insertPastedText(value: string) {
    if (!value) return;
    const textarea = composerRef.current;
    if (!textarea) {
      setComposer((current) => current + value);
      return;
    }
    const start = textarea.selectionStart ?? composer.length;
    const end = textarea.selectionEnd ?? start;
    setComposer((current) => `${current.slice(0, start)}${value}${current.slice(end)}`);
    window.requestAnimationFrame(() => {
      const cursor = start + value.length;
      textarea.focus();
      textarea.setSelectionRange(cursor, cursor);
    });
  }

  function handleComposerPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const imageFiles = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file" && AGENT_IMAGE_MEDIA_TYPES.has(item.type))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (imageFiles.length === 0) return;
    event.preventDefault();
    void addComposerImages(imageFiles);
    const text = event.clipboardData.getData("text/plain");
    insertPastedText(text);
  }

  async function retryMessage(message: AgentMessage) {
    const responseIndex = selectedMessages.findIndex((item) => item.message_id === message.message_id);
    if (responseIndex < 0) return;
    for (let index = responseIndex - 1; index >= 0; index -= 1) {
      const candidate = selectedMessages[index];
      if (candidate.role === "USER") {
        if (candidate.attachments.length === 0) {
          void sendMessage(candidate.content, undefined, researchConfigFromReceipt(candidate.model_receipt));
          return;
        }
        try {
          const attachments = await Promise.all(
            candidate.attachments.map(restoreImageAttachment),
          );
          void sendMessage(candidate.content, attachments, researchConfigFromReceipt(candidate.model_receipt));
        } catch (error) {
          setActionError(errorText(error, "Unable to reload the attached image."));
        }
        return;
      }
    }
    setActionError("The original prompt for this response is no longer loaded.");
  }

  async function retryFailedTurn() {
    if (!selectedId || !latestTurn || latestTurn.status !== "FAILED" || actionBusy) return;
    setActionError(null);
    await retryStream(
      selectedId,
      latestTurn.turn_id,
      (message) => setActionError(message),
    );
    await reloadDurableConversation(selectedId);
    await loadConversations();
  }

  async function reconnectCurrentTurn() {
    if (!selectedId || !latestTurn || !durableTurnActive || selectedStreaming) return;
    setActionBusy(`reconnect:${latestTurn.turn_id}`);
    setActionError(null);
    try {
      await reconnectTurn(
        selectedId,
        latestTurn.turn_id,
        latestTurn.status === "WAITING_TOOL" ? "tool" : "waiting",
      );
      await reloadDurableConversation(selectedId);
    } catch (error) {
      setActionError(errorText(error, "Unable to reconnect to this turn"));
      await reloadDurableConversation(selectedId);
    } finally {
      setActionBusy(null);
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
      setActionError(errorText(error, "Unable to save Copilot preferences"));
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
      setActionError(errorText(error, "Unable to reset Copilot preferences"));
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
      if (!isAbortError(error)) setStatusError(errorText(error, "Unable to read Copilot status"));
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
      if (!isAbortError(error)) setActionError(errorText(error, "Unable to load Copilot preferences"));
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
    };
  }, [loadConversations, loadPreferences, loadStatus]);

  useEffect(() => {
    if (!selectedId) return;
    void reloadDurableConversation(selectedId);
  }, [reloadDurableConversation, selectedId]);

  useEffect(() => {
    if (!selectedId || !latestTurn || !durableTurnActive || isStreaming(selectedId)) return;
    void reconnectTurn(
      selectedId,
      latestTurn.turn_id,
      latestTurn.status === "WAITING_TOOL" ? "tool" : "waiting",
    ).then(() => {
      void reloadDurableConversation(selectedId);
    });
    return () => abortReconnect(selectedId);
  }, [
    abortReconnect,
    durableTurnActive,
    isStreaming,
    latestTurn?.status,
    latestTurn?.turn_id,
    reconnectTurn,
    reloadDurableConversation,
    selectedId,
  ]);
  async function sendMessage(
    contentOverride?: string,
    attachmentsOverride?: AgentImageInput[],
    researchOverride?: CopilotResearchConfig,
  ) {
    const outgoingResearch = contentOverride !== undefined ? researchOverride : researchConfig;
    if (outgoingResearch && outgoingResearch.research_mode !== "standard" && (
      !Number.isInteger(outgoingResearch.research_max_seconds) || outgoingResearch.research_max_seconds < 30 || outgoingResearch.research_max_seconds > 600 ||
      !Number.isInteger(outgoingResearch.research_max_model_calls) || outgoingResearch.research_max_model_calls < 1 || outgoingResearch.research_max_model_calls > 16 ||
      !Number.isInteger(outgoingResearch.research_max_tool_calls) || outgoingResearch.research_max_tool_calls < 1 || outgoingResearch.research_max_tool_calls > 48
    )) { setActionError("Research budget must use whole numbers: 30–600 seconds, 1–16 model calls, and 1–48 tool calls."); return; }
    const content = (contentOverride ?? composer).trim();
    const outgoingImages = attachmentsOverride ?? composerImages;
    if (
      (!content && outgoingImages.length === 0)
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
    if (!conversationId || isStreaming(conversationId)) return;
    if (contentOverride === undefined) {
      setComposer("");
      setComposerImages([]);
    }
    setEditingMessageId(null);
    setActionError(null);
    const optimistic: AgentMessage = {
      message_id: `local-${crypto.randomUUID()}`,
      conversation_id: conversationId,
      role: "USER",
      content,
      attachments: [],
      sequence: null,
      created_at: new Date().toISOString(),
    };
    setMessages((current) => ({
      ...current,
      [conversationId]: [...(current[conversationId] ?? []), optimistic],
    }));
    await streamMessage({
      conversationId,
      content,
      attachments: outgoingImages.map(({ data_url, name }) => ({
        data_url,
        name,
      })),
      ephemeralContext,
      providerId: selectedProviderId || undefined,
      modelName: selectedModelName || undefined,
      reasoningEffort: selectedReasoningEffort || undefined,
      research: outgoingResearch,
    });
    await reloadDurableConversation(conversationId);
    await loadConversations();
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
      updateStream(selectedId, {
        pendingAction: null,
        pendingSummary: `${updated.capability}.${updated.operation}: ${updated.status}`,
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
      abortStream(selectedId);
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

  async function archiveConversation() {
    if (!selectedConversation || actionBusy) return;
    setActionBusy("archive");
    setActionError(null);
    try {
      await archiveAgentConversation(selectedConversation.conversation_id, selectedConversation.version);
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
    ? statusError || "Waiting for Copilot runtime diagnostics."
    : !status.enabled
      ? "Copilot runtime is disabled."
      : !status.configured
        ? "Copilot endpoint is not configured."
        : null;
  const canSend = Boolean(
    !disabledReason
    && !selectedStreaming
    && !durableTurnActive
    && !actionBusy
    && selectedProviderId
    && (selectedProviderId === "auto" || selectedModelName)
    && (composer.trim() || composerImages.length > 0),
  );
  const statusTone = disabledReason ? "attention" : "ready";

  return (
    <>
      <aside
        aria-label="Copilot Rail"
        aria-labelledby="console-agent-heading"
        aria-modal={overlayViewport && !collapsed ? true : undefined}
        className={`agent-rail${collapsed ? " collapsed" : ""}`}
        id="console-agent-panel"
        ref={railRef}
        role={overlayViewport ? "dialog" : "complementary"}
      >
        {!collapsed && !overlayViewport && (
          <ResizeHandle
            aria-label="Resize Copilot Panel"
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
          </ResizeHandle>
        )}
        {collapsed ? (
          <div className="agent-rail-collapsed-control">
            <IconButton
              aria-label="Open Copilot Panel"
              className="agent-rail-icon-button"
              onClick={() => onCollapsedChange(false)}
              title="Open Copilot Panel"
              type="button"
            >
              <PanelRightOpen aria-hidden="true" size={17} />
            </IconButton>
            <span className="agent-rail-collapsed-label">COPILOT</span>
          </div>
        ) : (
          <>
            <header className="agent-rail-header">
              <div className="agent-rail-heading">
                <p className="card-kicker">LOCAL RUNTIME</p>
                <h2 id="console-agent-heading">Copilot</h2>
              </div>
              <div className="agent-rail-header-actions">
                <span className={`agent-rail-status ${statusTone}`} aria-live="polite">
                  <Badge value={statusLabel(status, statusLoading)} tone={statusTone === "attention" ? "warn" : "good"} />
                </span>
                <IconButton
                  aria-label="Copilot Preferences"
                  aria-expanded={preferencesOpen}
                  className={`agent-rail-icon-button${preferencesOpen ? " active" : ""}`}
                  disabled={preferencesLoading}
                  onClick={() => { setPreferencesOpen((current) => !current); setHistoryOpen(false); }}
                  title="Presentation Preferences"
                  type="button"
                >
                  <Settings2 aria-hidden="true" size={14} />
                </IconButton>
                <IconButton
                  aria-label={focusMode ? "Exit Copilot Focus View" : "Expand Copilot Focus View"}
                  aria-pressed={focusMode}
                  className="agent-rail-icon-button"
                  disabled={overlayViewport}
                  onClick={() => setFocusMode((current) => !current)}
                  title={focusMode ? "Exit focus view" : "Focus view · 46% width"}
                  type="button"
                >
                  {focusMode ? <Minimize2 aria-hidden="true" size={14} /> : <Maximize2 aria-hidden="true" size={14} />}
                </IconButton>
                <IconButton
                  aria-label="Refresh Copilot Status"
                  className="agent-rail-icon-button"
                  disabled={statusLoading}
                  onClick={() => void loadStatus()}
                  title="Refresh Status"
                  type="button"
                >
                  <RefreshCw aria-hidden="true" size={14} />
                </IconButton>
                <IconButton
                  aria-label="Collapse Copilot Panel"
                  className="agent-rail-icon-button"
                  onClick={() => onCollapsedChange(true)}
                  title="Collapse Copilot Panel"
                  type="button"
                >
                  <PanelRightClose aria-hidden="true" size={16} />
                </IconButton>
              </div>
            </header>

            <div className="agent-rail-toolbar">
              <Button
                className="agent-rail-toolbar-button"
                disabled={actionBusy === "new"}
                onClick={() => void newConversation()}
                type="button"
              >
                <MessageSquarePlus aria-hidden="true" size={14} />
                New Session
              </Button>
              <Button
                aria-expanded={historyOpen}
                className={`agent-rail-toolbar-button${historyOpen ? " active" : ""}`}
                onClick={() => setHistoryOpen((current) => !current)}
                type="button"
              >
                <History aria-hidden="true" size={14} />
                History
                {historyOpen ? <ChevronUp aria-hidden="true" size={12} /> : <ChevronDown aria-hidden="true" size={12} />}
              </Button>
              <Button
                className="agent-rail-toolbar-button"
                disabled={!selectedConversation || actionBusy !== null}
                onClick={() => setArchiveConfirmation(true)}
                title="Archive This Session"
                type="button"
              >
                <Archive aria-hidden="true" size={13} />
                Archive
              </Button>
            </div>

            {visibleFailureNotice && !durableTurnActive && !selectedStreaming && (
              <AgentFailureNotification
                disabled={Boolean(disabledReason || selectedStreaming || actionBusy)}
                notice={visibleFailureNotice}
                onDismiss={dismissFailureNotice}
                onRetry={() => void retryFailedTurn()}
              />
            )}

            <div className="agent-rail-scroll" aria-live="polite">
              {!!runtimeComponents.length && !historyOpen && !preferencesOpen && (
                <Disclosure
                  className="agent-component-status"
                  title={`Runtime Components · ${runtimeComponents.filter(([, value]) => value.running).length}/${runtimeComponents.length} supervised`}
                  variant="compact"
                >
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
                </Disclosure>
              )}
              {preferencesOpen && preferenceDraft ? (
                <section className="agent-preferences" aria-label="Copilot Presentation Preferences">
                  <header>
                    <div><p className="card-kicker">PRESENTATION ONLY</p><h3>Copilot Preferences</h3></div>
                    <small>v{preferenceDraft.version}</small>
                  </header>
                  <p>Controls wording and presentation across Console. It cannot store prices, positions, orders, or research state.</p>
                  <div className="agent-preferences-grid">
                    <label><span><b className="required-mark" aria-hidden="true">*</b>Language</span><Select required value={preferenceDraft.language} onChange={(event) => updatePreferenceDraft("language", event.target.value as AgentPreferences["language"])}><option value="zh-CN">Simplified Chinese</option><option value="en">English</option></Select></label>
                    <label><span><b className="required-mark" aria-hidden="true">*</b>Answer Density</span><Select required value={preferenceDraft.response_density} onChange={(event) => updatePreferenceDraft("response_density", event.target.value as AgentPreferences["response_density"])}><option value="compact">Compact</option><option value="standard">Standard</option><option value="detailed">Detailed</option></Select></label>
                    <label><span><b className="required-mark" aria-hidden="true">*</b>Risk Wording</span><Select required value={preferenceDraft.risk_style} onChange={(event) => updatePreferenceDraft("risk_style", event.target.value as AgentPreferences["risk_style"])}><option value="balanced">Balanced</option><option value="cautious">Cautious</option><option value="direct">Direct</option></Select></label>
                    <label className="wide"><span>Preferred Source Codes</span><Input onChange={(event) => updatePreferenceDraft("preferred_source_codes", event.target.value.split(",").map((item) => item.trim()).filter(Boolean).slice(0, 32))} placeholder="SEC, FRED, YAHOO" value={preferenceDraft.preferred_source_codes.join(", ")} /></label>
                    <label className="toggle"><Input checked={preferenceDraft.default_chart} onChange={(event) => updatePreferenceDraft("default_chart", event.target.checked)} type="checkbox" /><span>Prefer a Chart When Useful</span></label>
                    <div className="agent-preference-default"><span>Web Search Background</span><strong>ON BY DEFAULT</strong></div>
                  </div>
                  <div className="agent-preferences-actions">
                    <Button size="sm" disabled={actionBusy !== null} onClick={() => void resetPreferences()} type="button"><RotateCcw aria-hidden="true" size={12} /> Reset</Button>
                    <Button className="primary" size="sm" disabled={actionBusy !== null} onClick={() => void savePreferences()} type="button"><Check aria-hidden="true" size={12} /> Save</Button>
                  </div>
                </section>
              ) : historyOpen ? (
                <div className="agent-rail-history" aria-label="Copilot Session History">
                  {conversationsLoading ? (
                    <div className="agent-rail-empty">Loading sessions…</div>
                  ) : conversationsError ? (
                    <div className="agent-rail-empty error">{conversationsError}</div>
                  ) : conversations.length === 0 ? (
                    <div className="agent-rail-empty">No sessions yet. Start typing below.</div>
                  ) : conversations.map((conversation) => (
                    <SelectableRow
                      selected={conversation.conversation_id === selectedId}
                      className={`agent-rail-history-row${conversation.conversation_id === selectedId ? " active" : ""}`}
                      key={conversation.conversation_id}
                      onClick={() => { setSelectedId(conversation.conversation_id); setHistoryOpen(false); }}
                      type="button"
                    >
                      <span>{initialTitle(conversation)}</span>
                      <small>{conversation.status} · {displayDate(conversation.updated_at)}</small>
                    </SelectableRow>
                  ))}
                </div>
              ) : (
                <>
                  {selectedMetrics && (
                    <div className="agent-metrics" aria-label="Conversation Usage">
                      <span><strong>{selectedMetrics.model_calls}</strong> Model Calls</span>
                      <span><strong>{selectedMetrics.total_tokens.toLocaleString("en-US")}</strong> tokens · reported usage</span>
                      <span><strong>{selectedMetrics.web_search_calls + selectedMetrics.web_extractor_calls}</strong> Web Calls</span>
                      <span><strong>{(selectedMetrics.latency_ms / 1000).toFixed(1)}s</strong> Model Time</span>
                      {selectedMetrics.truncated && <small>Bounded sample</small>}
                    </div>
                  )}
                  {selectedMetrics && <CopilotResearchProfiles profiles={selectedMetrics.research_profiles} />}
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
                          recoveredStatus={!selectedStreaming ? selectedTurns.find((turn) => turn.user_message_id === message.message_id && !turn.assistant_message_id)?.status : undefined}
                        />
                      ))}
                      {selectedStream?.research && selectedStream.phase !== "complete" && <CopilotResearchProgress research={selectedStream.research} />}
                      {selectedStream?.draft && (
                        <article className="agent-rail-message assistant live">
                          <header><span>Copilot · {selectedStream.phase === "tool" ? "tool status" : "streaming"}</span><span className="agent-rail-live">LIVE</span></header>
                          <AgentMessageContent content={selectedStream.draft} />
                          <AgentArtifactGallery urls={selectedStream.artifactUrls} />
                        </article>
                      )}
                      {!!selectedStream?.sourceUrls.length && (
                        <div className="agent-rail-source-block" aria-label="Current Web Sources">
                          <span>Current Sources</span>
                          {selectedStream.sourceUrls.map((url, index) => (
                            <TextLink href={url} key={url} rel="noopener noreferrer" target="_blank">
                              <ExternalLink aria-hidden="true" size={10} /> {index + 1}. {new URL(url).hostname}
                            </TextLink>
                          ))}
                        </div>
                      )}
                    </>
                  )}

                  {selectedStreaming && (
                    <div className="agent-rail-tool-state"><Wrench aria-hidden="true" size={13} /> {selectedStream?.phase === "tool" ? "Tool call in progress…" : "Waiting for Copilot…"}</div>
                  )}
                  {!selectedStreaming && durableTurnActive && (
                    <div className="agent-rail-tool-state">
                      <Wrench aria-hidden="true" size={13} />
                      <span>{latestTurn?.status === "WAITING_TOOL" ? "Tool work continues on the server…" : "This turn continues on the server…"}</span>
                      <Button disabled={actionBusy !== null} onClick={() => void reconnectCurrentTurn()} size="sm" type="button">Reconnect</Button>
                    </div>
                  )}
                  {!durableTurnActive && !failureNotice && latestTurn?.status === "FAILED" && latestTurn.error_code && (
                    <div className="agent-rail-error agent-turn-failed" role="status">
                      <span>Last Turn Failed · {latestTurn.error_code}</span>
                      <Button disabled={Boolean(disabledReason || selectedStreaming || actionBusy)} onClick={() => void retryFailedTurn()} size="sm" type="button">
                        <RefreshCw aria-hidden="true" size={11} /> Retry Turn
                      </Button>
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
                          <Button disabled={actionBusy !== null} onClick={() => void decidePendingAction("confirm")} size="sm" type="button"><Check aria-hidden="true" size={13} /> Confirm</Button>
                          <Button className="reject" disabled={actionBusy !== null} onClick={() => void decidePendingAction("reject")} size="sm" type="button"><X aria-hidden="true" size={13} /> Reject</Button>
                        </div>
                      ) : (
                        <Button className="agent-rail-resume-action" disabled={actionBusy !== null} onClick={() => void resumePendingAction()} size="sm" type="button">Resume Confirmation</Button>
                      )}
                    </div>
                  ) : selectedStream?.pendingSummary ? (
                    <div className="agent-rail-pending status"><strong>Action Status</strong><span>{selectedStream.pendingSummary}</span></div>
                  ) : null}
                  {messagesError && <div className="agent-rail-error" role="alert">{messagesError}</div>}
                  {actionError && <div className="agent-rail-error" role="alert">{actionError}</div>}
                  {statusError && <div className="agent-rail-error" role="status">{statusError}</div>}
                  {selectedStream?.error && !failureNotice && <div className="agent-rail-error" role="alert">{selectedStream.error}</div>}
                </>
              )}
            </div>

            <form className={`agent-rail-composer ${researchStyles.composer}`} onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
              <CopilotResearchControls config={researchConfig} disabled={Boolean(selectedStreaming || durableTurnActive || actionBusy)} onChange={setResearchConfig} />
              {editingMessageId && (
                <div className="agent-composer-editing" role="status">
                  Editing an earlier prompt. Send creates a new durable turn.
                  <Button aria-label="Cancel Editing" onClick={() => { setEditingMessageId(null); setComposer(""); setComposerImages([]); }} size="sm" type="button">Cancel</Button>
                </div>
              )}
              <div className="agent-rail-composer-frame">
                {composerImages.length > 0 && (
                  <div className="agent-composer-images" aria-label="Attached Images">
                    {composerImages.map((image) => (
                      <div className="agent-composer-image" key={image.id}>
                        <img alt={image.name || "Image to send"} src={image.data_url} />
                        <IconButton
                          aria-label={`Remove ${image.name || "attached image"}`}
                          onClick={() => setComposerImages((current) => current.filter((item) => item.id !== image.id))}
                          type="button"
                        >
                          <X aria-hidden="true" size={11} />
                        </IconButton>
                      </div>
                    ))}
                  </div>
                )}
                <Textarea
                  appearance="embedded"
                  aria-label="Message Copilot"
                  disabled={Boolean(disabledReason) || selectedStreaming || durableTurnActive || actionBusy !== null}
                  onChange={(event) => setComposer(event.target.value)}
                  onCompositionEnd={() => setComposerComposing(false)}
                  onCompositionStart={() => setComposerComposing(true)}
                  onKeyDown={handleComposerKeyDown}
                  onPaste={handleComposerPaste}
                  placeholder={disabledReason ?? (durableTurnActive ? "Current turn is still running…" : "Ask Copilot…")}
                  rows={3}
                  ref={composerRef}
                  value={composer}
                />
                {providerModelsError && (
                  <div className="agent-model-catalog-error" role="status">{providerModelsError}</div>
                )}
                <div className="agent-rail-composer-footer">
                  <div className="agent-composer-options">
                    <Input
                      accept="image/png,image/jpeg"
                      aria-label="Choose Images"
                      className="agent-image-file-input"
                      multiple
                      onChange={handleImageInput}
                      ref={imageInputRef}
                      type="file"
                    />
                    <Button
                      aria-label="Attach Images"
                      className="agent-composer-attach"
                      disabled={selectedStreaming || durableTurnActive || actionBusy !== null}
                      onClick={() => imageInputRef.current?.click()}
                      title="Attach PNG or JPEG images"
                      type="button"
                    >
                      <ImagePlus aria-hidden="true" size={13} />
                      <span>Image</span>
                    </Button>
                    <label className="agent-model-select agent-provider-select">
                      <span className="sr-only">Copilot Provider</span>
                      <Select
                        aria-label="Copilot Provider"
                        disabled={selectedStreaming || providerOptions.length < 2}
                        onChange={(event) => selectProvider(event.target.value)}
                        value={selectedProviderId}
                      >
                        {providerOptions.map((option) => (
                          <option key={option.id} value={option.id}>{option.provider}</option>
                        ))}
                      </Select>
                      <ChevronDown aria-hidden="true" size={13} />
                    </label>
                    <label className="agent-model-select agent-model-name-select">
                      <span className="sr-only">Copilot Model</span>
                      <Select
                        aria-label="Copilot Model"
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
                      </Select>
                      <ChevronDown aria-hidden="true" size={13} />
                    </label>
                    {selectedProviderId !== "auto" && reasoningOptions.length > 0 && (
                      <label className="agent-model-select agent-reasoning-select">
                        <span className="sr-only">Reasoning Effort</span>
                        <Select
                          aria-label="Reasoning Effort"
                          disabled={selectedStreaming}
                          onChange={(event) => selectReasoningEffort(event.target.value)}
                          value={selectedReasoningEffort}
                        >
                          <option value="">Auto</option>
                          {reasoningOptions.map((effort) => (
                            <option key={effort} value={effort}>{effort[0].toUpperCase() + effort.slice(1)}</option>
                          ))}
                        </Select>
                        <ChevronDown aria-hidden="true" size={13} />
                      </label>
                    )}
                  </div>
                  {selectedStreaming || durableTurnActive ? (
                    <IconButton
                      aria-label="Cancel Current Copilot Turn"
                      className="agent-rail-stop"
                      disabled={actionBusy !== null}
                      onClick={() => void cancelCurrentTurn()}
                      title="Cancel the Durable Server Turn"
                      type="button"
                    >
                      <Square aria-hidden="true" size={12} />
                    </IconButton>
                  ) : (
                    <IconButton aria-label="Send Message" className="agent-rail-send" disabled={!canSend} type="submit"><ArrowUp aria-hidden="true" size={15} /></IconButton>
                  )}
                </div>
              </div>
            </form>
          </>
        )}
      </aside>
      {collapsed && (
        <Button
          aria-label="Open Copilot Panel"
          className="agent-rail-mobile-tab"
          onClick={() => onCollapsedChange(false)}
          type="button"
        >
          <PanelRightOpen aria-hidden="true" size={16} />
          <span>Copilot</span>
        </Button>
      )}
      <ConfirmationDialog
        open={archiveConfirmation}
        title="Archive Copilot Conversation"
        description={`Archive “${selectedConversation ? initialTitle(selectedConversation) : "this conversation"}”? Messages and receipts remain durable and read-only.`}
        confirmLabel="Archive Conversation"
        tone="warning"
        busy={actionBusy === "archive"}
        onCancel={() => setArchiveConfirmation(false)}
        onConfirm={() => { setArchiveConfirmation(false); void archiveConversation(); }}
      />
    </>
  );
}
