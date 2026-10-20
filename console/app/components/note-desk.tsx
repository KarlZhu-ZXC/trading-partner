"use client";
import { useState } from "react";
import { useNoteInbox } from "./use-note-inbox";
import { NoteHistory } from "./note-history";
import { NoteConclusion } from "./note-conclusion";
import { NoteConversation } from "./note-conversation";
import { useApi, postApi, listOf } from "../lib/api";
import { asObject, textStringified as text } from "../lib/coerce";
import { Badge, Button, Card, Disclosure, Empty, ErrorNote, FormField, Input, LinkButton, Select, formatDate } from "./ui";
type Dict = Record<string, unknown>;
const CHANGE: Record<string, string> = { NEW_USER_NOTE: "New owner note", USER_TEXT_CHANGED: "Your text changed", USER_TEXT_UNCHANGED: "Your text unchanged", NO_USER_TEXT: "Quoted viewpoints only", CONTENT_UNAVAILABLE: "Full text unavailable", PREVIOUS_CONTENT_UNAVAILABLE: "Prior full text unavailable" };
const WARNING: Record<string, string> = {
  DURABLE_SNAPSHOTS_NOT_LIVE: "Account values are saved snapshots, not live quotes.",
  TRANSACTION_WINDOW_COVERAGE_UNVERIFIED: "The saved ledger does not prove complete broker history.",
  TRANSACTION_HISTORY_LIMIT_REACHED: "Older matching transactions exceed this view's history limit.",
  OTHER_OPTION_POSITIONS_NOT_ASSOCIATED: "Other option positions could not be associated with this note.",
  OPTION_MASTER_UNAVAILABLE: "Saved option metadata is unavailable; symbol hints remain unverified.",
  OPTION_MASTER_LIMIT_REACHED: "Option metadata exceeds this view's limit; option associations are withheld.",
  FUTURE_ACCOUNT_SNAPSHOT_EXCLUDED: "A snapshot with a future timestamp was excluded.",
  FUTURE_ACTIVITY_COVERAGE_EXCLUDED: "A source-window receipt with future facts was excluded.",
  ACTIVITY_COVERAGE_SCAN_LIMIT_REACHED: "The receipt lookup reached its limit; additional source windows may be missing.",
  ACTIVITY_COVERAGE_ACCOUNT_SCOPE_UNAVAILABLE: "The relevant saved account identities are unavailable.",
  ACTIVITY_COVERAGE_UNAVAILABLE: "Saved source-window receipts are unavailable; positions and transactions remain separate facts.",
  MOOMOO_NOTES_SUMMARY_ONLY: "Some source notes have summaries only.",
  MOOMOO_NOTES_FULL_TEXT_UNAVAILABLE: "Some full note bodies are unavailable.",
  OBSERVATION_OUT_OF_ORDER_IGNORED: "Older source revisions did not replace newer saved notes.",
  BROKER_COST_PNL_BASIS_MISMATCH: "Broker cost and reported P/L do not reconcile. Do not combine these into an exact return.",
};
function warningText(code: string) { return WARNING[code] ?? "Source details have limitations; inspect the source record before relying on them."; }
function NoteReader({ item, showHistory = true }: { item: Dict; showHistory?: boolean }) {
  const revision = text(item.note_revision_id);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [block, setBlock] = useState(0);
  const [offset, setOffset] = useState(0);
  const [readText, setReadText] = useState(false);
  const [contextCursor, setContextCursor] = useState<string | null>(null);
  const metadata = useApi<{ data: Dict }>(`/api/note-desk/${encodeURIComponent(revision)}${contextCursor ? `?context_cursor=${encodeURIComponent(contextCursor)}` : ""}`);
  const request = useApi<{ data: Dict }>(`/api/note-desk/${encodeURIComponent(revision)}?include_content=true&block_index=${block}&text_offset=${offset}`, { enabled: readText });
  const data = readText ? (request.data?.data?.note_revision_id === revision ? request.data.data : null) : metadata.data?.data;
  const context = asObject(metadata.data?.data?.portfolio_context);
  const coverage = asObject(context.transaction_coverage);
  const contextCurrent = (context.cursor ?? null) === contextCursor && !metadata.loading;

  const content = asObject(data?.content);
  return <section aria-label="Note Context" style={{ overflowWrap: "anywhere" }}>
    {request.error && <ErrorNote>Note context unavailable. Your source note remains unchanged.</ErrorNote>}
    <p>Revision {text(item.version)} · {formatDate(item.observed_at)}. Original note text is distinct from any confirmed investment judgment.</p>
    {!readText && item.coverage === "FULL" && <Button onClick={() => setReadText(true)}>Read Private Note Text</Button>}
    {readText && data && <>
      {block > 0 && <Button onClick={() => { setBlock(block - 1); setOffset(0); }}>Previous Attributed Section</Button>}
      <FormField label="Attributed Section"><Select value={block} onChange={e => { setBlock(Number(e.target.value)); setOffset(0); }}>{listOf<Dict>(data, "blocks").map(b => <option key={String(b.index)} value={Number(b.index)}>{text(b.section_date, "Undated")} · {text(b.speaker_label)} · {text(b.speaker_kind)}</option>)}</Select></FormField>
      {!request.loading && content.block_index === block && content.text_offset === offset && typeof content.text === "string" && <><Badge value={text(content.speaker_kind)} /><p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{text(content.text)}</p></>}
      {content.content_redacted === true && <p>Secret-like values and URLs are hidden in this reading view.</p>}
      {offset > 0 && <Button disabled={request.loading} onClick={() => setOffset(0)}>Back to Section Start</Button>}
      {content.next_text_offset != null && <Button disabled={request.loading} onClick={() => setOffset(Number(content.next_text_offset))}>Continue This Section</Button>}
      {content.next_block_index != null && <Button disabled={request.loading} onClick={() => { setBlock(Number(content.next_block_index)); setOffset(0); }}>Next Attributed Section</Button>}
    </>}
    <Disclosure title="Trades and Holdings" defaultOpen>
      <p>Saved account context across accounts. Times and currencies remain separate; matching a symbol does not link a trade to this note.</p>
      {metadata.error && <ErrorNote>Account context changed or could not be read. Reload the first page.</ErrorNote>}
      {context.status === "UNMAPPED" && <p>This note has no exact instrument mapping. No position match is inferred from its title.</p>}
      {context.status === "UNAVAILABLE" && <p>Account context unavailable.</p>}
      {context.positions_status === "UNAVAILABLE" && <p>Position snapshots unavailable; this does not mean you have no holdings.</p>}
      {context.transactions_status === "UNAVAILABLE" && <p>Saved transactions unavailable.</p>}
      {contextCurrent && listOf<Dict>(context, "items").map((entry, i) => {
        const row = asObject(entry.kind === "POSITION" ? entry.position : entry.transaction);
        const option = asObject(entry.option);
        return <article key={`${contextCursor ?? "first"}-${i}`}>
          <strong>{text(row.instrument_id)} · {entry.kind === "POSITION" ? "Holding" : "Recorded Transaction"}</strong>
          <p>{text(entry.provider ?? row.provider)} · {text(entry.account_ref ?? row.account_ref)} · {text(row.side)} · {text(row.quantity)} · {text(row.currency)}</p>
          {entry.kind === "POSITION" ? <p>Broker average cost: {text(row.average_cost, "Unavailable")} · Snapshot: {formatDate(entry.fetched_at)} · Market value: {text(row.market_value, "Unavailable")}</p> : <p>Price: {text(row.price, "Unavailable")} · Fees: {text(row.fees, "Unavailable")} · {formatDate(row.occurred_at)}</p>}
          {entry.option != null && <p>{text(option.option_type, "Type unavailable")} · {text(option.expiration_date, "Expiry unavailable")} · Strike {text(option.strike, "Unavailable")}. {option.identity_basis === "DURABLE_INSTRUMENT_MASTER" ? `Saved instrument relation · recorded multiplier ${text(option.multiplier, "Unavailable")}. Contract terms and Greeks are not fully verified.` : "Symbol-derived identity; multiplier and Greeks require verified contract data."}</p>}
          {listOf<string>(entry, "warning_codes").map(code => <p key={code}>{warningText(code)}</p>)}
        </article>;
      })}
      <p>History coverage: {text(context.history_coverage, "Unavailable")}. These snapshots are not live. {listOf<string>(context, "warning_codes").map(warningText).join(" ")}</p>
      <Disclosure title="Recorded Transaction Coverage">
        <p>Saved source windows do not prove complete account history.</p>
        {contextCurrent && listOf<Dict>(coverage, "receipts").map(receipt => <article key={text(receipt.receipt_id)}>
          <strong>{text(receipt.provider)} · {text(receipt.account_ref)}</strong>
          <p>Recorded window: {formatDate(receipt.effective_start)} – {formatDate(receipt.effective_end)} · {text(receipt.status)}</p>
          <p>Requested window: {formatDate(receipt.requested_start)} – {formatDate(receipt.requested_end)} · Retrieved {formatDate(receipt.fetched_at)}</p>
          {listOf<string>(receipt, "gap_codes").map(code => <p key={code}>{warningText(code)}</p>)}
          {listOf<string>(receipt, "unavailable_kinds").length > 0 && <p>Unavailable activity types: {listOf<string>(receipt, "unavailable_kinds").join(", ")}</p>}
        </article>)}
        {contextCurrent && listOf<Dict>(coverage, "receipts").length === 0 && <p>No saved source-window receipt is available for these accounts.</p>}
        {coverage.has_more === true && <p>More receipts may exist outside this bounded view.</p>}
        {listOf<string>(coverage, "warning_codes").map(code => <p key={code}>{warningText(code)}</p>)}
      </Disclosure>
      {contextCursor && <Button onClick={() => setContextCursor(null)}>First Account Context Page</Button>}
      {contextCurrent && typeof context.next_cursor === "string" && <Button disabled={metadata.loading} onClick={() => setContextCursor(text(context.next_cursor))}>More Trades and Holdings</Button>}
    </Disclosure>
    {item.coverage === "FULL" && <LinkButton href={`/decision-workbench?note_revision_id=${encodeURIComponent(revision)}#notes`}>Review This Note in Journal</LinkButton>}
    {item.coverage === "FULL" && <Disclosure title="My Conclusion">
      <NoteConclusion key={revision} revision={revision} onSaved={() => setHistoryVersion(value => value + 1)} />
    </Disclosure>}
    {showHistory && <Disclosure title="Thinking History" onToggle={setHistoryOpen}>
      {historyOpen && <NoteHistory key={`${revision}:${historyVersion}`} revision={revision}
        renderReader={row => <NoteReader key={text(row.note_revision_id)} item={row} showHistory={false} />} />}
    </Disclosure>}
    <Disclosure title="Optional Discussion">
      <NoteConversation revision={revision} coverage={item.coverage} />
    </Disclosure>
    <Button onClick={() => { setContextCursor(null); metadata.refresh(); if (readText) request.refresh(); }}>Reload Note Context</Button>
  </section>;
}
export function NoteDesk() {
  const [query, setQuery] = useState("");
  const [draftQuery, setDraftQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const request = useNoteInbox(query, offset);
  const [selected, setSelected] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const items = listOf<Dict>(request.data?.data, "items");
  const visible = items.filter(item => all || !["USER_TEXT_UNCHANGED", "NO_USER_TEXT"].includes(text(item.change)));
  async function sync() {
    setSyncing(true); setMessage(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "external_state_sync", confirmation: "external_state_sync", arguments: { request: { operation: "notes" } }, preserve_full_result: true });
      if (asObject(response.result).ok !== true) throw new Error("Note sync is unavailable. Existing synced notes remain readable.");
      const receipt = asObject(asObject(response.result).data);
      const warnings = listOf<string>(receipt, "warning_codes");
      const incomplete = receipt.status === "PARTIAL" || warnings.length > 0;
      setMessage(incomplete
        ? `Sync incomplete. ${warnings.includes("MOOMOO_NOTES_REMOTE_AUTH_REQUIRED") ? "Moomoo remote authentication is unavailable; local cached notes remain readable. " : ""}Cloud changes are not confirmed. ${warnings.filter(code => code !== "MOOMOO_NOTES_REMOTE_AUTH_REQUIRED").map(warningText).join(" ")}`
        : ["SUCCEEDED", "SUCCESS", "COMPLETE"].includes(text(receipt.status))
          ? "Notes synced. No model analysis, account refresh or judgment update was requested."
          : "Sync coverage is unconfirmed. Reload saved notes to inspect available revisions.");
      request.refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Sync unavailable"); }
    finally { setSyncing(false); }
  }
  return <Card kicker="MOOMOO" title="Your Notes" action={<Button disabled={syncing} onClick={() => { void sync(); }}>{syncing ? "Syncing…" : "Sync Moomoo Notes"}</Button>}>
    <p>Use your Moomoo notes directly and check saved trades and holdings when needed. Discussion with Codex is optional; no discussion record or daily status update is required.</p>
    <form onSubmit={event => { event.preventDefault(); setQuery(draftQuery.trim()); setOffset(0); setSelected(null); setAll(true); }}>
      <FormField label="Find Saved Notes"><Input value={draftQuery} maxLength={200} placeholder="Title or instrument" onChange={event => setDraftQuery(event.target.value)} /></FormField>
      <Button type="submit">Search Notes</Button>{" "}
      {query && <Button onClick={() => { setQuery(""); setDraftQuery(""); setOffset(0); setSelected(null); }}>Clear Search</Button>}
    </form>
    <Button disabled={request.loading} onClick={request.refresh}>Reload Saved Notes</Button>{" "}<Button onClick={() => setAll(v => !v)}>{all ? "Show Owner Changes" : "Show All Notes on This Page"}</Button>
    {request.error && <ErrorNote>Saved notes could not be read. This does not mean the inbox is empty.</ErrorNote>}
    {message && <p role="status">{message}</p>}
    {!request.loading && !request.error && !request.data?.data && <ErrorNote>Note source coverage is unavailable.</ErrorNote>}
    {!request.loading && !request.error && request.data?.data && visible.length === 0 && <Empty>{all ? "No matching notes on this page." : "No owner text changes in the available synced notes. Use Show All Notes on This Page to read unchanged notes and quoted viewpoints."} No daily status update is required.</Empty>}
    {request.data?.data?.discussion_coverage === "UNAVAILABLE" && <p>Saved discussion history is unavailable. Available notes remain readable.</p>}
    {request.loading && <p role="status">Loading saved notes…</p>}
    {offset > 0 && <Button disabled={request.loading} onClick={() => { setOffset(Math.max(0, offset - 30)); setSelected(null); }}>Newer Notes</Button>}
    {request.data?.data?.has_more === true && <Button disabled={request.loading} onClick={() => { setOffset(offset + 30); setSelected(null); }}>Older Notes</Button>}
    {visible.map(item => <article key={text(item.note_revision_id)} style={{ overflowWrap: "anywhere" }}><h3>{text(item.title)}</h3><p>{text(item.instrument_id, "Unassigned instrument")} · {formatDate(item.observed_at)} · {CHANGE[text(item.change)] ?? "Change unavailable"}{item.discussion ? " · Discussion recorded" : ""}</p><Button onClick={() => setSelected(selected === item.note_revision_id ? null : text(item.note_revision_id))}>{selected === item.note_revision_id ? "Close Note" : "Open Note"}</Button>{selected === item.note_revision_id && <NoteReader key={selected} item={item} />}</article>)}
    <Disclosure title="Optional Research Workspace"><LinkButton href="/research">Open Research Archives</LinkButton><p>Use structured Thesis and Trade Plan workflows only when you need to formalize a judgment or execution plan. Reading a note does not require either.</p></Disclosure>
  </Card>;
}
