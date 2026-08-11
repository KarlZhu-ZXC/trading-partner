import assert from "node:assert/strict";
import test from "node:test";
import { createSseParser, parseSseEvents } from "../app/lib/sse.mjs";

test("SSE parser joins chunk boundaries, CRLF lines, and multiline data", () => {
  const events = [];
  const parser = createSseParser({ onEvent: (event) => events.push(event) });
  parser.push("event: text_delta\r");
  parser.push("\nid: 7\r\ndata: {\"delta\":\"hello ");
  parser.push("world\"}\r\n\r\n");
  parser.end();

  assert.deepEqual(events, [
    {
      event: "text_delta",
      id: "7",
      data: '{"delta":"hello world"}',
      payload: { delta: "hello world" },
    },
  ]);
});

test("SSE parser dispatches an EOF event and preserves invalid JSON", () => {
  const result = parseSseEvents("event: failed\ndata: {not-json}");
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].event, "failed");
  assert.equal(result.events[0].data, "{not-json}");
  assert.equal(result.events[0].payload, "{not-json}");
  assert.match(result.events[0].jsonError, /Unexpected|JSON/i);
});
