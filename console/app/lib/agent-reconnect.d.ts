export type AgentReconnectOptions = {
  attempts?: number;
  delays?: number[];
  signal?: AbortSignal;
  sleep?: (delayMs: number, signal?: AbortSignal) => Promise<unknown>;
  retryable?: (error: unknown) => boolean;
};

export function reconnectAgentStreamWithBackoff(
  connect: (attempt: number) => Promise<void>,
  options?: AgentReconnectOptions,
): Promise<void>;
