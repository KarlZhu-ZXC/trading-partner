function abortError() {
  return new DOMException("The Agent reconnect was aborted", "AbortError");
}

function defaultRetryable(error) {
  if (error && typeof error === "object") {
    if (error.name === "AbortError") return false;
    if (error.retryable === true) return true;
    if (Number.isInteger(error.status)) {
      return error.status === 408 || error.status === 429 || error.status >= 500;
    }
  }
  return true;
}

function wait(delayMs, signal) {
  if (signal?.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const finish = () => {
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const timer = setTimeout(finish, delayMs);
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(abortError());
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

/**
 * Retry only the durable replay endpoint. It never resends the user message or
 * re-enters the Provider, so a browser disconnect cannot duplicate a turn.
 */
export async function reconnectAgentStreamWithBackoff(connect, options = {}) {
  const attempts = Math.max(1, Math.min(4, options.attempts ?? 3));
  const delays = options.delays ?? [200, 600, 1_500];
  const sleep = options.sleep ?? wait;
  const retryable = options.retryable ?? defaultRetryable;
  let lastError = new Error("Agent reconnect did not run");
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (options.signal?.aborted) throw abortError();
    try {
      await connect(attempt + 1);
      return;
    } catch (error) {
      lastError = error;
      if (!retryable(error) || attempt + 1 >= attempts) throw error;
      await sleep(delays[Math.min(attempt, delays.length - 1)] ?? 0, options.signal);
    }
  }
  throw lastError;
}
