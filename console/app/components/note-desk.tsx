"use client";
import { useState } from "react";
import { useApi, postApi, listOf } from "../lib/api";
import { asObject, textStringified as text } from "../lib/coerce";
import { Badge, Button, Card, Disclosure, Empty, ErrorNote, FormField, LinkButton, Select, formatDate } from "./ui";
type Dict = Record<string, unknown>;
const CHANGE: Record<string, string> = { NEW_USER_NOTE: "New owner note", USER_TEXT_CHANGED: "Your text changed", USER_TEXT_UNCHANGED: "Your text unchanged", NO_USER_TEXT: "Quoted viewpoints only", CONTENT_UNAVAILABLE: "Full text unavailable", PREVIOUS_CONTENT_UNAVAILABLE: "Prior full text unavailable" };
function NoteReader({ item }: { item: Dict }) {
  const revision = text(item.note_revision_id);
  const [block, setBlock] = useState(0);
  const [offset, setOffset] = useState(0);
  const [readText, setReadText] = useState(false);
  const request = useApi<{ data: Dict }>(`/api/note-desk/${encodeURIComponent(revision)}?include_content=${readText}&block_index=${block}&text_offset=${offset}`);
  const data = request.data?.data?.note_revision_id === revision ? request.data.data : null;
  const content = asObject(data?.content);
  return <section aria-label="Note Conversation Context" style={{ overflowWrap: "anywhere" }}>
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
    <p>In Codex, ask to read this exact note and compare your latest thinking. To retain a conclusion, explicitly ask to record it. No daily status update is required.</p>
    <code>{revision}</code>
    <Disclosure title="Saved Discussions">{data?.discussion_coverage === "UNAVAILABLE" ? <p>Discussion history is unavailable; this does not mean there are no saved discussions.</p> : listOf<Dict>(data, "discussion_history").length === 0 ? <p>No discussion found in the available history.</p> : listOf<Dict>(data, "discussion_history").map(j => <div key={text(j.journal_id)}><strong>{text(j.title)}</strong><p>{formatDate(j.created_at)} · {text(j.note_revision_id)}</p>{typeof j.body_preview === "string" && <p>{text(j.body_preview)}{j.body_truncated ? " … (preview)" : ""}</p>}</div>)}{data?.discussion_coverage === "PARTIAL" && <p>Discussion history is bounded; older records may be omitted.</p>}</Disclosure>
    <Button onClick={request.refresh}>Reload Discussion Context</Button>
  </section>;
}
export function NoteDesk() {
  const request = useApi<{ data: Dict }>("/api/note-desk");
  const [selected, setSelected] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const items = listOf<Dict>(request.data?.data, "items");
  const visible = items.filter(item => all || (!item.discussion && !["USER_TEXT_UNCHANGED", "NO_USER_TEXT"].includes(text(item.change))));
  async function sync() {
    setSyncing(true); setMessage(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: "external_state_sync", confirmation: "external_state_sync", arguments: { request: { operation: "notes" } }, preserve_full_result: true });
      if (asObject(response.result).ok !== true) throw new Error("Note sync is unavailable. Existing synced notes remain readable.");
      setMessage("Notes synced. No model analysis, account refresh or judgment update was requested."); request.refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Sync unavailable"); }
    finally { setSyncing(false); }
  }
  return <Card kicker="MOOMOO → CODEX" title="Your Notes" action={<Button disabled={syncing} onClick={() => { void sync(); }}>{syncing ? "Syncing…" : "Sync Moomoo Notes"}</Button>}>
    <p>Write in Moomoo, discuss in Codex. These are note changes, not a checklist to clear. Research archives support the conversation in the background.</p>
    <Button disabled={request.loading} onClick={request.refresh}>Reload Saved Notes</Button>{" "}<Button onClick={() => setAll(v => !v)}>{all ? "Show Owner Changes" : "Show All Recent Notes"}</Button>
    {request.error && <ErrorNote>Saved notes could not be read. This does not mean the inbox is empty.</ErrorNote>}
    {message && <p role="status">{message}</p>}
    {!request.loading && !request.error && !request.data?.data && <ErrorNote>Note source coverage is unavailable.</ErrorNote>}
    {!request.loading && !request.error && request.data?.data && visible.length === 0 && <Empty>No undiscussed owner changes in the available synced notes. No archive status maintenance is required.</Empty>}
    {request.data?.data?.discussion_coverage === "UNAVAILABLE" && <p>Saved discussions could not be read; note changes are not treated as already discussed.</p>}
    {request.data?.data?.has_more === true && <p>Recent notes are bounded; older notes may be outside this view.</p>}
    {visible.map(item => <article key={text(item.note_revision_id)} style={{ overflowWrap: "anywhere" }}><h3>{text(item.title)}</h3><p>{text(item.instrument_id, "Unassigned instrument")} · {formatDate(item.observed_at)} · {CHANGE[text(item.change)] ?? "Change unavailable"}{item.discussion ? " · Discussion recorded" : ""}</p><Button onClick={() => setSelected(selected === item.note_revision_id ? null : text(item.note_revision_id))}>{selected === item.note_revision_id ? "Close Note" : "Open Note"}</Button>{selected === item.note_revision_id && <NoteReader key={selected} item={item} />}</article>)}
    <Disclosure title="Optional Research Workspace"><LinkButton href="/research">Open Research Archives</LinkButton><p>Use structured Thesis and Trade Plan workflows only when you need to formalize a judgment or execution plan. Reading a note does not require either.</p></Disclosure>
  </Card>;
}
