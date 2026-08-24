import {
  AgentPendingAction,
  AgentFailureNotice,
  AgentReceipt,
  AgentStreamEvent,
  parsePendingAction,
  parseAgentFailureNotice,
  parseReceipt,
} from "./agent-api";
import { asRecord, textStrict as text } from "./coerce";

type Dict = Record<string, unknown>;

export type AgentStreamPhase = "waiting" | "tool" | "streaming" | "complete";

/**
 * Canonical stream state for the Agent Rail. Keeping the lifecycle and
 * confirmation boundary here prevents a second UI controller from emerging.
 */
export type AgentStreamSnapshot = {
  turnId: string | null;
  phase: AgentStreamPhase;
  draft: string;
  receipts: AgentReceipt[];
  sourceUrls: string[];
  artifactUrls: string[];
  chartLinks: string[];
  researchSubjectIds: string[];
  pendingSummary: string | null;
  pendingAction: { action: AgentPendingAction; token: string } | null;
  error: string | null;
  failureNotice: AgentFailureNotice | null;
};

export const EMPTY_AGENT_STREAM: AgentStreamSnapshot = {
  turnId: null,
  phase: "waiting",
  draft: "",
  receipts: [],
  sourceUrls: [],
  artifactUrls: [],
  chartLinks: [],
  researchSubjectIds: [],
  pendingSummary: null,
  pendingAction: null,
  error: null,
  failureNotice: null,
};

export type AgentStreamLinkMode = "none" | "permissive" | "safe";

export type AgentStreamReducerOptions = {
  toolSourceLinks?: AgentStreamLinkMode;
  completedSourceLinks?: AgentStreamLinkMode;
  artifactLinks?: AgentStreamLinkMode;
  includeChartLinks?: boolean;
  includeResearchSubjectIds?: boolean;
  pendingActionFallback?: string;
};

const DEFAULT_REDUCER_OPTIONS: Required<AgentStreamReducerOptions> = {
  toolSourceLinks: "none",
  completedSourceLinks: "safe",
  artifactLinks: "none",
  includeChartLinks: false,
  includeResearchSubjectIds: false,
  pendingActionFallback: "This action requires an explicit decision.",
};

export function mergeAgentReceipts(items: AgentReceipt[]): AgentReceipt[] {
  const byId = new Map<string, AgentReceipt>();
  for (const item of items) {
    if (item.receipt_id) byId.set(item.receipt_id, item);
  }
  return Array.from(byId.values()).sort((left, right) =>
    String(right.created_at ?? "").localeCompare(String(left.created_at ?? "")),
  );
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

export function extractResearchSubjectIds(value: unknown): string[] {
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

function linksFor(value: unknown, mode: AgentStreamLinkMode, localOnly = false): string[] {
  if (mode === "none") return [];
  return mode === "permissive" ? collectLinks(value) : safeLinks(value, localOnly);
}

function eventPayload(event: AgentStreamEvent): Dict {
  return asRecord(event.payload);
}

/**
 * Apply one SSE event without coupling either UI shell to transport parsing.
 * Link extraction is intentionally configurable because Chat historically
 * displayed broader artifact/source links than the compact Rail.
 */
export function reduceAgentStream(
  snapshot: AgentStreamSnapshot,
  event: AgentStreamEvent,
  options: AgentStreamReducerOptions = {},
): AgentStreamSnapshot {
  const config = { ...DEFAULT_REDUCER_OPTIONS, ...options };
  if (event.jsonError) {
    return { ...snapshot, error: "The Agent stream returned an invalid event payload." };
  }

  const payload = eventPayload(event);
  switch (event.event) {
    case "message_started":
      return {
        ...snapshot,
        turnId: text(payload.turn_id) || null,
        phase: "waiting",
        error: null,
        failureNotice: null,
      };
    case "tool_started":
      return { ...snapshot, phase: "tool", error: null };
    case "tool_finished": {
      const receipt = parseReceipt(payload.receipt ?? payload);
      if (!receipt) return snapshot;
      const sourceUrls = [
        ...linksFor(payload.source_urls, config.toolSourceLinks),
        ...linksFor(payload, config.toolSourceLinks === "permissive" ? "permissive" : "none"),
      ];
      const artifactUrls = linksFor(
        payload.artifact_url,
        config.artifactLinks,
        config.artifactLinks === "safe",
      );
      const chartLinks = config.includeChartLinks
        ? linksFor(payload.chart_artifact, "permissive")
        : [];
      const subjectIds = config.includeResearchSubjectIds
        ? [...extractResearchSubjectIds(payload), ...extractResearchSubjectIds(receipt)]
        : [];
      return {
        ...snapshot,
        phase: "tool",
        receipts: mergeAgentReceipts([...snapshot.receipts, receipt]),
        sourceUrls: Array.from(new Set([...snapshot.sourceUrls, ...sourceUrls])),
        artifactUrls: Array.from(new Set([...snapshot.artifactUrls, ...artifactUrls])).slice(0, 20),
        chartLinks: Array.from(new Set([...snapshot.chartLinks, ...chartLinks])),
        researchSubjectIds: Array.from(new Set([...snapshot.researchSubjectIds, ...subjectIds])),
      };
    }
    case "text_delta": {
      const delta = firstText(payload, ["text_delta", "delta", "text", "content"])
        || (typeof event.payload === "string" ? event.payload : "");
      return delta
        ? { ...snapshot, phase: "streaming", draft: snapshot.draft + delta, error: null }
        : snapshot;
    }
    case "pending_action": {
      const action = parsePendingAction(payload.pending_action ?? payload);
      const token = text(payload.confirmation_token);
      return {
        ...snapshot,
        phase: "tool",
        pendingSummary: action?.presented_summary
          || firstText(payload, ["presented_summary", "summary", "message"])
          || config.pendingActionFallback,
        pendingAction: action && token ? { action, token } : null,
      };
    }
    case "completed": {
      const finalText = firstText(payload, ["text", "content", "answer"])
        || (typeof event.payload === "string" ? event.payload : "");
      const sourceValue = payload.web_source_urls ?? payload.source_urls;
      const sourceUrls = linksFor(sourceValue, config.completedSourceLinks);
      const artifactUrls = linksFor(payload.artifact_urls, config.artifactLinks, config.artifactLinks === "safe");
      const chartLinks = config.includeChartLinks
        ? linksFor(payload.chart_artifact, "permissive")
        : [];
      const subjectIds = config.includeResearchSubjectIds ? extractResearchSubjectIds(payload) : [];
      return {
        ...snapshot,
        phase: "complete",
        draft: finalText || snapshot.draft,
        sourceUrls: config.completedSourceLinks === "safe" && sourceUrls.length === 0
          ? snapshot.sourceUrls
          : Array.from(new Set([...snapshot.sourceUrls, ...sourceUrls])),
        artifactUrls: config.artifactLinks === "safe" && artifactUrls.length === 0
          ? snapshot.artifactUrls
          : Array.from(new Set([...snapshot.artifactUrls, ...artifactUrls])).slice(0, 20),
        chartLinks: Array.from(new Set([...snapshot.chartLinks, ...chartLinks])),
        researchSubjectIds: Array.from(new Set([...snapshot.researchSubjectIds, ...subjectIds])),
        failureNotice: null,
      };
    }
    case "failed": {
      const failureNotice = parseAgentFailureNotice(payload.notification);
      return {
        ...snapshot,
        phase: "complete",
        failureNotice,
        error: failureNotice
          ? null
          : firstText(payload, ["message", "detail", "error", "code"])
            || "The Agent stream failed before completion.",
      };
    }
    case "cancelled":
      return {
        ...snapshot,
        phase: "complete",
        error: null,
        pendingSummary: "This turn was cancelled. No new tool call will be started.",
      };
    default:
      return snapshot;
  }
}
