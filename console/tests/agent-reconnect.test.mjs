import assert from "node:assert/strict";
import test from "node:test";

import { reconnectAgentStreamWithBackoff } from "../app/lib/agent-reconnect.mjs";

test("durable reconnect retries transient failures without resending a turn", async () => {
  const attempts = [];
  const delays = [];
  await reconnectAgentStreamWithBackoff(
    async (attempt) => {
      attempts.push(attempt);
      if (attempt < 3) {
        const error = new Error("temporary");
        error.status = 503;
        throw error;
      }
    },
    {
      attempts: 3,
      delays: [20, 40],
      sleep: async (delay) => { delays.push(delay); },
    },
  );
  assert.deepEqual(attempts, [1, 2, 3]);
  assert.deepEqual(delays, [20, 40]);
});

test("durable reconnect does not retry a terminal client error", async () => {
  let calls = 0;
  await assert.rejects(
    reconnectAgentStreamWithBackoff(async () => {
      calls += 1;
      const error = new Error("not found");
      error.status = 404;
      throw error;
    }),
    /not found/,
  );
  assert.equal(calls, 1);
});

test("durable reconnect obeys browser cancellation", async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    reconnectAgentStreamWithBackoff(async () => {}, { signal: controller.signal }),
    (error) => error instanceof DOMException && error.name === "AbortError",
  );
});
