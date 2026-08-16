"use client";

import { useMemo, useState } from "react";
import { ConsoleShell } from "../components/console-shell";
import { ActionButton, Badge, Card, DataBoundary, FieldLabel, RefreshButton, displayJson } from "../components/ui";
import { postApi, useApi } from "../lib/api";

type Capability = {
  name: string;
  group: string;
  description: string;
  operations: string[];
  read_only: boolean;
  open_world: boolean;
  destructive: boolean;
  effect: string;
  confirmation_required: boolean;
  input_schema: Record<string, unknown>;
};

type JsonSchema = Record<string, unknown>;
type Dict = Record<string, unknown>;

function dereference(schema: JsonSchema, root: JsonSchema): JsonSchema {
  const reference = schema.$ref;
  if (typeof reference !== "string" || !reference.startsWith("#/$defs/")) return schema;
  const name = reference.slice("#/$defs/".length);
  const definitions = root.$defs;
  if (!definitions || typeof definitions !== "object") return schema;
  const target = (definitions as Record<string, unknown>)[name];
  return target && typeof target === "object" ? target as JsonSchema : schema;
}

function placeholderFor(schema: JsonSchema, root: JsonSchema, key: string): unknown {
  const resolved = dereference(schema, root);
  if ("const" in resolved) return resolved.const;
  if (key === "idempotency_key") return `console-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  if (key === "confirmed_by" || key === "reviewed_by") return "user";
  const anyOf = resolved.anyOf;
  if (Array.isArray(anyOf)) {
    const candidate = anyOf.find((item) => item && typeof item === "object" && (item as JsonSchema).type !== "null");
    if (candidate && typeof candidate === "object") return placeholderFor(candidate as JsonSchema, root, key);
  }
  if (resolved.type === "array") return [];
  if (resolved.type === "object") return requiredObject(resolved, root);
  if (resolved.type === "boolean") return false;
  if (resolved.type === "integer" || resolved.type === "number") return 0;
  return "";
}

function requiredObject(schema: JsonSchema, root: JsonSchema): Record<string, unknown> {
  const properties = schema.properties;
  const required = Array.isArray(schema.required) ? schema.required.filter((item): item is string => typeof item === "string") : [];
  if (!properties || typeof properties !== "object") return {};
  const values: Record<string, unknown> = {};
  for (const key of required) {
    const property = (properties as Record<string, unknown>)[key];
    if (property && typeof property === "object") values[key] = placeholderFor(property as JsonSchema, root, key);
  }
  return values;
}

function argumentsTemplate(schema: JsonSchema, operation?: string): Record<string, unknown> {
  if (!operation) return requiredObject(schema, schema);
  const definitions = schema.$defs;
  if (definitions && typeof definitions === "object") {
    for (const candidate of Object.values(definitions as Record<string, unknown>)) {
      if (!candidate || typeof candidate !== "object") continue;
      const properties = (candidate as JsonSchema).properties;
      if (!properties || typeof properties !== "object") continue;
      const operationSchema = (properties as Record<string, unknown>).operation;
      if (operationSchema && typeof operationSchema === "object" && (operationSchema as JsonSchema).const === operation) {
        return { request: requiredObject(candidate as JsonSchema, schema) };
      }
    }
  }
  return { request: { operation } };
}

function toolImages(value: unknown): Array<{ data: string; mimeType: string }> {
  const images: Array<{ data: string; mimeType: string }> = [];
  function visit(candidate: unknown) {
    if (Array.isArray(candidate)) {
      candidate.forEach(visit);
      return;
    }
    if (!candidate || typeof candidate !== "object") return;
    const record = candidate as Record<string, unknown>;
    const mimeType = typeof record.mimeType === "string" ? record.mimeType : typeof record.mime_type === "string" ? record.mime_type : "";
    if (record.type === "image" && typeof record.data === "string" && mimeType.startsWith("image/")) {
      images.push({ data: record.data, mimeType });
      return;
    }
    Object.values(record).forEach(visit);
  }
  visit(value);
  return images;
}

function MarketLens() {
  const [market, setMarket] = useState("US");
  const [query, setQuery] = useState("TTWO");
  const [instrumentId, setInstrumentId] = useState("equity:US:TTWO");
  const [running, setRunning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const images = useMemo(() => toolImages(result), [result]);

  async function invoke(toolName: string, argumentsValue: Dict) {
    setRunning(toolName); setError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: toolName, arguments: argumentsValue });
      setResult(response);
      if (toolName === "instrument_resolve") {
        const envelope = response.result as Dict | undefined;
        const data = envelope?.data as Dict | undefined;
        const resolved = data?.instrument_id ?? (data?.instrument as Dict | undefined)?.instrument_id;
        if (typeof resolved === "string") setInstrumentId(resolved);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Fact retrieval failed"); }
    finally { setRunning(null); }
  }

  return <Card className="market-lens" kicker="MARKET & TECHNICAL LENS" title="Quick Facts Workspace"><p className="card-note">Resolve an instrument, then retrieve its current quote, daily/weekly technical snapshot, or chart. Every result preserves source, fact time, and warnings; no trading instruction is generated.</p><div className="market-lens-controls"><label><FieldLabel required>Market</FieldLabel><select required value={market} onChange={(event) => setMarket(event.target.value)}>{["US", "A_SHARE", "KR", "CME", "OTC"].map((item) => <option key={item}>{item}</option>)}</select></label><label><FieldLabel required>Symbol / Query</FieldLabel><input required value={query} onChange={(event) => setQuery(event.target.value)} /></label><ActionButton busy={running === "instrument_resolve"} onClick={() => { void invoke("instrument_resolve", { market, query, asset_type: null }); }}>Resolve</ActionButton><label className="market-lens-instrument"><FieldLabel required>Instrument ID</FieldLabel><input required value={instrumentId} onChange={(event) => setInstrumentId(event.target.value)} /></label><ActionButton busy={running === "market_data_get"} onClick={() => { void invoke("market_data_get", { request: { operation: "quote", instrument_id: instrumentId } }); }}>Quote</ActionButton><ActionButton busy={running === "technical_get_snapshot"} onClick={() => { void invoke("technical_get_snapshot", { instrument_id: instrumentId, lookback_sessions: 260, intervals: ["1d", "1w"] }); }}>Technical</ActionButton><ActionButton busy={running === "technical_render_chart"} onClick={() => { void invoke("technical_render_chart", { instrument_id: instrumentId, interval: "1d", lookback_sessions: 160 }); }}>Chart</ActionButton></div>{error && <div className="inline-error">{error}</div>}{images.length > 0 && <div className="market-lens-images">{images.map((item, index) => <img alt={`${instrumentId} technical chart ${index + 1}`} key={`${item.mimeType}-${index}`} src={`data:${item.mimeType};base64,${item.data}`} />)}</div>}{result !== null && <details className="run-receipt" open><summary>Fact Receipt</summary><pre>{displayJson(result)}</pre></details>}</Card>;
}

export default function CapabilitiesPage() {
  const result = useApi<{ count: number; items: Capability[] }>("/api/capabilities");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Capability | null>(null);
  const [argumentsText, setArgumentsText] = useState("{}");
  const [confirmed, setConfirmed] = useState(false);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<unknown>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const items = useMemo(() => result.data?.items ?? [], [result.data?.items]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? items.filter((item) => [item.name, item.group, item.description, ...item.operations].join(" ").toLowerCase().includes(needle)) : items;
  }, [items, query]);
  const groups = useMemo(() => Map.groupBy(filtered, (item) => item.group), [filtered]);
  const images = useMemo(() => toolImages(runResult), [runResult]);

  function openWorkbench(capability: Capability, operation?: string) {
    setSelected(capability);
    setArgumentsText(
      JSON.stringify(argumentsTemplate(capability.input_schema, operation), null, 2),
    );
    setConfirmed(false);
    setRunError(null);
    setRunResult(null);
    setCopyState("idle");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function copyResult() {
    if (runResult === null) return;
    try {
      await navigator.clipboard.writeText(displayJson(runResult));
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  async function invoke() {
    if (!selected) return;
    let argumentsValue: Record<string, unknown>;
    try {
      argumentsValue = JSON.parse(argumentsText) as Record<string, unknown>;
    } catch {
      setRunError("Arguments are not valid JSON.");
      return;
    }
    if (selected.confirmation_required && !confirmed) {
      setRunError("Explicit confirmation is required. The backend still enforces its idempotency and user-confirmation checks.");
      return;
    }
    setRunning(true);
    setRunError(null);
    try {
      const value = await postApi<unknown>("/api/tools/invoke", {
        tool_name: selected.name,
        arguments: argumentsValue,
        confirmation: selected.confirmation_required ? selected.name : null,
      });
      setRunResult(value);
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Execution failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <ConsoleShell active="capabilities">
      <DataBoundary loading={result.loading} error={result.error}>
        <MarketLens />
        {selected && (
          <Card className="workbench" kicker="MCP TOOL WORKBENCH" title={selected.name} action={<button className="close-button" type="button" onClick={() => setSelected(null)}>Close</button>}>
            <div className="workbench-grid">
              <div>
                <p className="workbench-help">Invoke the same public MCP adapter used by Codex. Required fields are prefilled from the schema; write tools never bypass candidate review, idempotency keys, or actor gates.</p>
                {selected.operations.length > 0 && <div className="operation-picker">{selected.operations.map((operation) => <button key={operation} type="button" onClick={() => openWorkbench(selected, operation)}>{operation}</button>)}</div>}
                <label className="editor-label" htmlFor="tool-arguments"><b className="required-mark" aria-hidden="true">*</b>Arguments JSON</label>
                <textarea required id="tool-arguments" className="json-editor" spellCheck={false} value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} />
                {selected.confirmation_required && <label className="confirmation-check"><input required type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span><b className="required-mark" aria-hidden="true">*</b>I explicitly request this controlled action and understand that the tool&apos;s own confirmation fields still apply.</span></label>}
                <div className="workbench-actions"><ActionButton onClick={invoke} busy={running} tone={selected.destructive ? "warning" : "default"}>Run Tool</ActionButton><Badge value={selected.confirmation_required ? "CONFIRM" : selected.effect} /></div>
                {runError && <div className="inline-error">{runError}</div>}
              </div>
              <div>
                <div className="result-head"><span>RESULT</span>{runResult !== null && <button type="button" onClick={copyResult}>{copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy Failed" : "Copy"}</button>}</div>
                <span className="sr-only" aria-live="polite">{copyState === "copied" ? "Result Copied" : copyState === "failed" ? "Copy Failed" : ""}</span>
                {images.length > 0 && <div className="tool-images">{images.map((item, index) => (
                  <img alt={`Technical chart ${index + 1}`} key={`${item.mimeType}-${index}`} src={`data:${item.mimeType};base64,${item.data}`} />
                ))}</div>}
                <pre className="result-view">{runResult === null ? "Waiting to run…" : displayJson(runResult)}</pre>
                <details className="schema-details"><summary>View Input Schema</summary><pre>{displayJson(selected.input_schema)}</pre></details>
              </div>
            </div>
          </Card>
        )}
        <div className="toolbar capability-toolbar">
          <div className="search-box"><span>⌕</span><input aria-label="Search Capabilities" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search tools, operations, or descriptions…" /></div>
          <div className="toolbar-count"><strong>{filtered.length}</strong> / {result.data?.count ?? 0} tools</div>
          <RefreshButton onClick={result.refresh} loading={result.loading} />
        </div>
        <div className="capability-groups">
          {[...groups.entries()].map(([group, capabilities]) => (
            <section key={group}>
              <header><h2>{group}</h2><span>{capabilities.length}</span></header>
              <div className="capability-grid">
                {capabilities.map((capability) => (
                  <Card className="capability-card" key={capability.name}>
                    <div className="capability-title"><code>{capability.name}</code><Badge value={capability.confirmation_required ? "CONFIRM" : capability.effect} /></div>
                    <p>{capability.description || "No description."}</p>
                    <div className="operation-pills">
                      {capability.operations.length ? capability.operations.map((operation) => <button type="button" onClick={() => openWorkbench(capability, operation)} key={operation}>{operation}</button>) : <button type="button" onClick={() => openWorkbench(capability)}>Open Tool</button>}
                    </div>
                    <footer><span>{capability.open_world ? "Provider access" : "Local state"}</span><button type="button" onClick={() => openWorkbench(capability)}>Open →</button></footer>
                  </Card>
                ))}
              </div>
            </section>
          ))}
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
