/**
 * Tiny Server-Sent Events parser for the Agent stream.
 *
 * It intentionally does not depend on EventSource: Agent messages are POST
 * requests carrying the Console session token.  The parser accepts arbitrary
 * chunk boundaries, CRLF/LF lines, multiline data fields, and an unterminated
 * final event.  Invalid JSON is returned as raw text with `jsonError` instead
 * of terminating the stream.
 */

function parseEvent(eventName, eventId, dataLines) {
  if (dataLines.length === 0) return null;
  const data = dataLines.join("\n");
  let payload = data;
  let jsonError;
  try {
    payload = JSON.parse(data);
  } catch (error) {
    jsonError = error instanceof Error ? error.message : "Invalid JSON";
  }
  return {
    event: eventName || "message",
    ...(eventId ? { id: eventId } : {}),
    data,
    payload,
    ...(jsonError ? { jsonError } : {}),
  };
}

function readField(line) {
  if (line.startsWith(":")) return null;
  const separator = line.indexOf(":");
  if (separator < 0) return { field: line, value: "" };
  let value = line.slice(separator + 1);
  if (value.startsWith(" ")) value = value.slice(1);
  return { field: line.slice(0, separator), value };
}

export function createSseParser({ onEvent } = {}) {
  let buffer = "";
  let eventName = "";
  let eventId = "";
  let dataLines = [];
  let ended = false;

  const dispatch = () => {
    const parsed = parseEvent(eventName, eventId, dataLines);
    eventName = "";
    eventId = "";
    dataLines = [];
    if (!parsed) return;
    if (typeof onEvent === "function") onEvent(parsed);
  };

  const consumeLine = (line) => {
    if (line === "") {
      dispatch();
      return;
    }
    const parsed = readField(line);
    if (!parsed) return;
    if (parsed.field === "event") eventName = parsed.value;
    else if (parsed.field === "id") eventId = parsed.value;
    else if (parsed.field === "data") dataLines.push(parsed.value);
    // `retry` and extension fields are deliberately ignored.  The Agent
    // stream has no client-side reconnection protocol.
  };

  const push = (chunk) => {
    if (ended) return;
    if (chunk === null || chunk === undefined) return;
    buffer += String(chunk);
    while (buffer.length > 0) {
      let newline = -1;
      let width = 1;
      for (let index = 0; index < buffer.length; index += 1) {
        const character = buffer[index];
        if (character === "\n") {
          newline = index;
          break;
        }
        if (character === "\r") {
          // A CR at the end of a chunk might be the first half of CRLF.  Keep
          // it until the next push so the following LF is not misread as an
          // empty line.
          if (index === buffer.length - 1) return;
          newline = index;
          width = buffer[index + 1] === "\n" ? 2 : 1;
          break;
        }
      }
      if (newline < 0) return;
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + width);
      consumeLine(line);
    }
  };

  const end = (tail = "") => {
    if (ended) return;
    if (tail) push(tail);
    ended = true;
    // A final line is valid even when the server omitted the terminating
    // blank line.  CR is also a legal SSE line ending.
    if (buffer.length > 0) {
      consumeLine(buffer.replace(/\r$/, ""));
      buffer = "";
    }
    dispatch();
  };

  return { push, feed: push, end };
}

export function parseSseEvents(text) {
  const events = [];
  const errors = [];
  // The parser reports failures by throwing from push/end; there is no
  // separate onError channel on createSseParser.
  const parser = createSseParser({
    onEvent: (event) => events.push(event),
  });
  try {
    parser.push(text);
    parser.end();
  } catch (error) {
    errors.push(error);
  }
  return { events, errors };
}

// The alias is convenient for small consumers and keeps the public parser
// vocabulary obvious in tests and diagnostics.
export const parseSseText = parseSseEvents;
