"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  reconnectAgentTurnStream,
  retryAgentTurnStream,
  streamAgentMessage,
  type AgentEphemeralContext,
  type AgentImageInput,
  type AgentStreamEvent,
} from "./agent-api";
import {
  EMPTY_AGENT_STREAM,
  reduceAgentStream,
  type AgentStreamReducerOptions,
  type AgentStreamSnapshot,
} from "./agent-stream";
import { reconnectAgentStreamWithBackoff } from "./agent-reconnect.mjs";

type StreamMode = "send" | "retry" | "reconnect";

type StreamSettledCallback = (
  conversationId: string,
  mode: StreamMode,
  completed: boolean,
) => void | Promise<void>;

type StreamErrorCallback = (message: string) => void;

export type SendAgentMessageOptions = {
  conversationId: string;
  content: string;
  ephemeralContext?: AgentEphemeralContext;
  providerId?: string;
  modelName?: string;
  reasoningEffort?: string;
  attachments?: AgentImageInput[];
};

export type AgentConversationHookOptions = {
  reducer?: AgentStreamReducerOptions;
  onSettled?: StreamSettledCallback;
};

export type AgentConversationController = {
  streams: Record<string, AgentStreamSnapshot>;
  isStreaming: (conversationId: string) => boolean;
  updateStream: (conversationId: string, patch: Partial<AgentStreamSnapshot>) => void;
  handleStreamEvent: (conversationId: string, event: AgentStreamEvent) => void;
  sendMessage: (options: SendAgentMessageOptions, onError?: StreamErrorCallback) => Promise<boolean>;
  retryTurn: (
    conversationId: string,
    turnId: string,
    onError?: StreamErrorCallback,
  ) => Promise<boolean>;
  reconnectTurn: (
    conversationId: string,
    turnId: string,
    initialPhase?: AgentStreamSnapshot["phase"],
  ) => Promise<boolean>;
  abortStream: (conversationId: string, clear?: boolean) => void;
  abortReconnect: (conversationId: string) => void;
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/**
 * Owns the single Agent Rail transport controller and stream reducer. Durable
 * messages, receipts, and confirmation decisions remain in the Rail view;
 * `/chat` is only a compatibility redirect and has no conversation controller.
 */
export function useAgentConversation(
  options: AgentConversationHookOptions = {},
): AgentConversationController {
  const [streams, setStreams] = useState<Record<string, AgentStreamSnapshot>>({});
  const [, setActiveVersion] = useState(0);
  const activeStreamsRef = useRef(new Set<string>());
  const controllers = useRef(new Map<string, AbortController>());
  const reconnectControllers = useRef(new Map<string, AbortController>());
  const durableTurnIds = useRef(new Map<string, string>());
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const markActive = useCallback((conversationId: string, active: boolean) => {
    if (active) activeStreamsRef.current.add(conversationId);
    else activeStreamsRef.current.delete(conversationId);
    setActiveVersion((current) => current + 1);
  }, []);

  const updateStream = useCallback((conversationId: string, patch: Partial<AgentStreamSnapshot>) => {
    setStreams((current) => ({
      ...current,
      [conversationId]: { ...(current[conversationId] ?? EMPTY_AGENT_STREAM), ...patch },
    }));
  }, []);

  const handleStreamEvent = useCallback((conversationId: string, event: AgentStreamEvent) => {
    const payload = event.payload && typeof event.payload === "object"
      ? event.payload as Record<string, unknown>
      : {};
    if (event.event === "message_started" && typeof payload.turn_id === "string") {
      durableTurnIds.current.set(conversationId, payload.turn_id);
    } else if (["completed", "cancelled", "failed"].includes(event.event)) {
      durableTurnIds.current.delete(conversationId);
    }
    setStreams((current) => ({
      ...current,
      [conversationId]: reduceAgentStream(
        current[conversationId] ?? EMPTY_AGENT_STREAM,
        event,
        optionsRef.current.reducer,
      ),
    }));
  }, []);

  const finishController = useCallback((
    conversationId: string,
    controller: AbortController,
    reconnect: boolean,
  ) => {
    const map = reconnect ? reconnectControllers.current : controllers.current;
    if (map.get(conversationId) === controller) map.delete(conversationId);
    if (!controllers.current.has(conversationId) && !reconnectControllers.current.has(conversationId)) {
      markActive(conversationId, false);
    }
  }, [markActive]);

  const runStream = useCallback(async (
    mode: StreamMode,
    conversationId: string,
    turnId: string | null,
    content: string | null,
    streamOptions: Omit<SendAgentMessageOptions, "conversationId" | "content"> = {},
    onError?: StreamErrorCallback,
  ) => {
    const reconnect = mode === "reconnect";
    const map = reconnect ? reconnectControllers.current : controllers.current;
    if (
      controllers.current.has(conversationId)
      || reconnectControllers.current.has(conversationId)
    ) return false;
    const controller = new AbortController();
    map.set(conversationId, controller);
    markActive(conversationId, true);
    if (mode !== "reconnect") {
      setStreams((current) => ({
        ...current,
        [conversationId]: { ...EMPTY_AGENT_STREAM, phase: "waiting" },
      }));
    }
    let completed = false;
    try {
      if (mode === "send") {
        await streamAgentMessage(
          conversationId,
          content ?? "",
          (event) => handleStreamEvent(conversationId, event),
          controller.signal,
          streamOptions.ephemeralContext,
          streamOptions.providerId,
          streamOptions.modelName,
          streamOptions.reasoningEffort,
          streamOptions.attachments,
        );
      } else if (mode === "retry") {
        await retryAgentTurnStream(
          conversationId,
          turnId ?? "",
          (event) => handleStreamEvent(conversationId, event),
          controller.signal,
        );
      } else {
        await reconnectAgentTurnStream(
          conversationId,
          turnId ?? "",
          (event) => handleStreamEvent(conversationId, event),
          controller.signal,
        );
      }
      completed = true;
    } catch (error) {
      let finalError = error;
      const durableTurnId = durableTurnIds.current.get(conversationId)
        ?? (mode === "reconnect" ? turnId : null);
      if (!isAbortError(error) && !controller.signal.aborted && durableTurnId) {
        try {
          await reconnectAgentStreamWithBackoff(
            () => reconnectAgentTurnStream(
              conversationId,
              durableTurnId,
              (event) => handleStreamEvent(conversationId, event),
              controller.signal,
            ),
            { signal: controller.signal },
          );
          completed = true;
        } catch (reconnectError) {
          finalError = reconnectError;
        }
      }
      if (!completed && !isAbortError(finalError) && !controller.signal.aborted) {
        const fallback = mode === "reconnect"
          ? "Unable to reconnect to this turn"
          : mode === "retry"
            ? "Unable to retry this turn"
            : "The Agent stream could not be started.";
        const message = errorText(finalError, fallback);
        if (mode === "send" || mode === "reconnect") {
          updateStream(conversationId, { phase: "complete", error: message });
        }
        onError?.(message);
      }
    } finally {
      finishController(conversationId, controller, reconnect);
      if (!controller.signal.aborted && mode === "send") {
        setStreams((current) => {
          const existing = current[conversationId];
          if (!existing) return current;
          return {
            ...current,
            [conversationId]: { ...existing, draft: "", phase: "complete" },
          };
        });
      }
      if (!controller.signal.aborted && (completed || mode === "reconnect")) {
        await optionsRef.current.onSettled?.(conversationId, mode, completed);
      }
    }
    return completed && !controller.signal.aborted;
  }, [finishController, handleStreamEvent, markActive, updateStream]);

  const sendMessage = useCallback((
    sendOptions: SendAgentMessageOptions,
    onError?: StreamErrorCallback,
  ) => runStream(
    "send",
    sendOptions.conversationId,
    null,
    sendOptions.content,
    sendOptions,
    onError,
  ), [runStream]);

  const retryTurn = useCallback((
    conversationId: string,
    turnId: string,
    onError?: StreamErrorCallback,
  ) => runStream("retry", conversationId, turnId, null, {}, onError), [runStream]);

  const reconnectTurn = useCallback((
    conversationId: string,
    turnId: string,
    initialPhase: AgentStreamSnapshot["phase"] = "waiting",
  ) => {
    const previous = reconnectControllers.current.get(conversationId);
    previous?.abort();
    if (reconnectControllers.current.get(conversationId) === previous) {
      reconnectControllers.current.delete(conversationId);
    }
    updateStream(conversationId, { turnId, phase: initialPhase });
    return runStream("reconnect", conversationId, turnId, null);
  }, [runStream, updateStream]);

  const abortReconnect = useCallback((conversationId: string) => {
    const controller = reconnectControllers.current.get(conversationId);
    controller?.abort();
    finishController(conversationId, controller ?? new AbortController(), true);
  }, [finishController]);

  const abortStream = useCallback((conversationId: string, clear = false) => {
    controllers.current.get(conversationId)?.abort();
    reconnectControllers.current.get(conversationId)?.abort();
    controllers.current.delete(conversationId);
    reconnectControllers.current.delete(conversationId);
    markActive(conversationId, false);
    if (clear) {
      setStreams((current) => {
        const next = { ...current };
        delete next[conversationId];
        return next;
      });
    }
  }, [markActive]);

  useEffect(() => () => {
    controllers.current.forEach((controller) => controller.abort());
    reconnectControllers.current.forEach((controller) => controller.abort());
    durableTurnIds.current.clear();
  }, []);

  return {
    streams,
    isStreaming: useCallback((conversationId: string) => activeStreamsRef.current.has(conversationId), []),
    updateStream,
    handleStreamEvent,
    sendMessage,
    retryTurn,
    reconnectTurn,
    abortStream,
    abortReconnect,
  };
}
