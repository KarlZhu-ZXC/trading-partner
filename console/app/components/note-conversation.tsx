"use client";

import { useState } from "react";
import { useApi, listOf } from "../lib/api";
import { asObject, textStringified as text } from "../lib/coerce";
import { Button, Disclosure, ErrorNote, FormField, Textarea, formatDate } from "./ui";

type Dict = Record<string, unknown>;

export function SavedNoteEntry({ revision, journal, label = "Discussion" }: { revision: string; journal: Dict; label?: "Discussion" | "Conclusion" }) {
  const journalId = text(journal.journal_id);
  const [offset, setOffset] = useState(0);
  const request = useApi<{ data: Dict }>(
    `/api/note-desk/${encodeURIComponent(revision)}?include_content=true&journal_id=${encodeURIComponent(journalId)}&text_offset=${offset}`,
  );
  const content = asObject(request.data?.data?.discussion_content);
  const current = request.data?.data?.note_revision_id === revision
    && content.journal_id === journalId
    && content.note_revision_id === journal.note_revision_id
    && content.text_offset === offset;
  const next = content.next_text_offset;
  return <section aria-label={`Saved ${label} Text`}>
    <p>{journal.note_revision_id === revision ? "Saved for this note revision." : "Saved for another note revision."} This record does not update a formal investment judgment.</p>
    <Disclosure title="Source Revision" variant="compact"><code>{text(journal.note_revision_id)}</code></Disclosure>
    {request.loading && <p role="status">Loading saved {label.toLowerCase()}…</p>}
    {request.error || (!request.loading && (!current || typeof content.text !== "string"))
      ? <><ErrorNote>Saved {label.toLowerCase()} unavailable. Retry this read; do not record it again.</ErrorNote><Button onClick={request.refresh}>Retry {label} Read</Button></>
      : !request.loading && <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{text(content.text)}</p>}
    {offset > 0 && <Button disabled={request.loading} onClick={() => setOffset(0)}>Back to {label} Start</Button>}
    {!request.error && !request.loading && current && typeof next === "number" && Number.isInteger(next) && next > offset
      && <Button onClick={() => setOffset(next)}>Continue Saved {label}</Button>}
  </section>;
}

export function NoteConversation({ revision, coverage }: {
  revision: string;
  coverage: unknown;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const request = useApi<{ data: Dict }>(
    `/api/note-desk/${encodeURIComponent(revision)}?include_discussions=true&include_portfolio=false`,
    { enabled: historyOpen },
  );
  const metadata = request.data?.data?.note_revision_id === revision ? request.data.data : undefined;
  const { loading, error } = request;
  const [copyState, setCopyState] = useState<"idle" | "copied" | "manual">("idle");
  const [selectedJournal, setSelectedJournal] = useState<string | null>(null);
  const prompt = coverage === "FULL"
    ? `Use Trading Partner to read the private Moomoo note at exact revision ${JSON.stringify(revision)}, keeping my words separate from quoted viewpoints. Compare it with available saved discussions, cross-account holdings and related transactions, preserving source times and coverage gaps. Discuss the changes with me. Record a conclusion only if I explicitly ask; this request does not authorize a broker refresh, model analysis workflow, formal judgment update or order.`
    : `Use Trading Partner to inspect metadata for the Moomoo note at exact revision ${JSON.stringify(revision)} and its available saved account context. Full note text is unavailable; do not infer it from the summary. Discuss the available context with me; this request does not authorize a sync, model analysis workflow, saved conclusion, formal judgment update or order.`;
  async function copyRequest() {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopyState("copied");
    } catch {
      setCopyState("manual");
    }
  }
  const history = listOf<Dict>(metadata, "discussion_history").filter(entry => entry.entry_kind !== "PERSONAL_CONCLUSION");
  return <>
    <p>Optionally discuss this exact note in Codex. Your note can stand on its own; save a discussion conclusion only when you want to retain it.</p>
    <Button onClick={() => { void copyRequest(); }}>Copy Codex Request</Button>
    {copyState === "copied" && <p role="status">Request copied. Paste it into your Codex conversation when ready. No note text or account values were copied.</p>}
    {copyState === "manual" && <><p role="status">Clipboard unavailable. Select and copy the request below.</p><FormField label="Codex Request"><Textarea readOnly value={prompt} rows={5} /></FormField></>}
    <Disclosure title="Saved Discussions" onToggle={setHistoryOpen}>
      {loading && <p role="status">Loading discussion history…</p>}
      {error || metadata?.discussion_coverage === "UNAVAILABLE"
        ? <><p>Discussion history is unavailable; this does not mean there are no saved discussions.</p><Button onClick={request.refresh}>Retry Discussion History</Button></>
        : !loading && metadata && history.length === 0 && <p>No discussion found in the available history.</p>}
      {history.map(journal => {
        const id = text(journal.journal_id);
        return <article key={id}>
          <strong>{text(journal.title)}</strong>
          <p>{formatDate(journal.created_at)} · {journal.note_revision_id === revision ? "This note revision" : "Another note revision"}</p>
          <Button onClick={() => setSelectedJournal(current => current === id ? null : id)}>
            {selectedJournal === id ? "Close Saved Discussion" : "Read Saved Discussion"}
          </Button>
          {selectedJournal === id && <SavedNoteEntry key={id} revision={revision} journal={journal} />}
        </article>;
      })}
      {metadata?.discussion_coverage === "PARTIAL" && <p>Discussion history is bounded; older records may be omitted.</p>}
    </Disclosure>
  </>;
}
