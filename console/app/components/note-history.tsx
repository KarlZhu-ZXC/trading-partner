"use client";

import { useState, type ReactNode } from "react";
import { listOf, useApi } from "../lib/api";
import { asObject, textStringified as text } from "../lib/coerce";
import { Button, Disclosure, ErrorNote, formatDate } from "./ui";
import { SavedNoteEntry } from "./note-conversation";

type Dict = Record<string, unknown>;

function HistoryTrades({ revision, start, end }: { revision: string; start: string; end: string }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const query = new URLSearchParams({ start, end });
  if (cursor) query.set("cursor", cursor);
  const request = useApi<{ data: Dict }>(`/api/note-desk/${encodeURIComponent(revision)}/history-trades?${query}`);
  const data = request.data?.data;
  const current = !request.loading && !request.error && data
    && (data.cursor ?? null) === cursor;
  const coverage = asObject(data?.transaction_coverage);
  const rows = current ? listOf<Dict>(data, "items") : [];
  return <section aria-label="Transactions in This Period">
    <p>{formatDate(start)} – {formatDate(end)}. Saved activity in this period is shown for comparison. Timing does not establish that a trade followed a note.</p>
    {request.loading && <p role="status">Loading saved transactions…</p>}
    {request.error && <ErrorNote>Transactions changed or could not be read. Reload the first page.</ErrorNote>}
    {current && (data.transactions_status === "UNAVAILABLE" || data.status === "UNAVAILABLE") && <p>Saved transactions are unavailable.</p>}
    {current && data.transactions_status !== "UNAVAILABLE" && data.status !== "UNAVAILABLE" && rows.length === 0 && <p>No matching transactions in the available saved records. This does not establish complete broker history.</p>}
    {rows.map((entry, index) => {
      const row = asObject(entry.transaction), option = asObject(entry.option);
      return <article key={index}>
        <strong>{text(row.instrument_id)} · {text(row.side ?? row.kind)}</strong>
        <p>{formatDate(row.occurred_at)} · {text(row.provider)} · {text(row.account_ref)}</p>
        <p>Quantity {text(row.quantity, "Unavailable")} · Price {text(row.price, "Unavailable")} · Fees {text(row.fees, "Unavailable")} · {text(row.currency)}</p>
        <p>Source: {text(row.source_type, "Unavailable")}</p>
        {entry.option != null && <p>{text(option.option_type, "Type unavailable")} · Expiry {text(option.expiration_date, "Unavailable")} · Strike {text(option.strike, "Unavailable")} · Recorded multiplier {text(option.multiplier, "Unavailable")}. {option.identity_basis === "DURABLE_INSTRUMENT_MASTER" ? "Saved instrument relation; complete contract terms remain unverified." : "Symbol-derived relation; contract terms are unverified."}</p>}
      </article>;
    })}
    {current && <>
      <p>History coverage: {text(data.history_coverage, "Unavailable")}. Native currencies remain separate.</p>
      {listOf<string>(data, "warning_codes").map(code => <p key={code}>{code.replaceAll("_", " ").toLowerCase()}.</p>)}
      <Disclosure title="Source Windows">
        {listOf<Dict>(coverage, "receipts").map(receipt => <p key={text(receipt.receipt_id)}>
          {text(receipt.provider)} · {text(receipt.account_ref)} · {formatDate(receipt.effective_start)} – {formatDate(receipt.effective_end)} · Retrieved {formatDate(receipt.fetched_at)} · {text(receipt.status)}
          {listOf<string>(receipt, "gap_codes").concat(listOf<string>(receipt, "unavailable_kinds")).join(", ")}
        </p>)}
        {listOf<Dict>(coverage, "receipts").length === 0 && <p>No saved source-window receipt is available.</p>}
        {coverage.has_more === true && <p>Additional source windows may be outside this bounded view.</p>}
      </Disclosure>
    </>}
    {(cursor || request.error) && <Button onClick={() => { setCursor(null); request.refresh(); }}>Reload First Transaction Page</Button>}
    {current && typeof data.next_cursor === "string" && <Button onClick={() => setCursor(text(data.next_cursor))}>More Transactions in This Period</Button>}
  </section>;
}

export function NoteHistory({ revision, renderReader }: { revision: string; renderReader: (item: Dict) => ReactNode }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showTrades, setShowTrades] = useState(false);
  const request = useApi<{ data: Dict }>(`/api/note-desk/${encodeURIComponent(revision)}/history${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`);
  const data = request.data?.data;
  const current = !request.loading && !request.error && data?.note_revision_id === revision && (data.cursor ?? null) === cursor;
  const items = current ? listOf<Dict>(data, "items") : [];
  return <section aria-label="Thinking History">
    <p>Notes, their saved versions and personal records, newest first. {data?.scope === "NOTE" ? "This note has no exact Instrument mapping, so only its own history is shown." : "Grouped by the notes’ current exact Instrument mapping; this does not reconstruct historical mappings."}</p>
    {request.loading && <p role="status">Loading thinking history…</p>}
    {request.error && <ErrorNote>History changed or could not be read. Reload the first page.</ErrorNote>}
    {current && data?.has_older_revisions === true && <p>This view includes the latest 1,000 source revisions; older revisions are outside its coverage.</p>}
    {current && data?.journals_status !== "AVAILABLE" && <p>Saved conclusions and discussions have {text(data?.journals_status).toLowerCase()} coverage. Missing rows do not mean no records were saved.</p>}
    {current && items.length === 0 && <p>No history found in the available records.</p>}
    {items.map(row => {
      const id = text(row.event_id), note = row.kind === "NOTE_REVISION", personal = row.kind === "PERSONAL_CONCLUSION";
      return <article key={id}>
        <strong>{note ? `Note · Version ${text(row.version)}` : personal ? "Personal Conclusion" : "Saved Discussion"}</strong>
        <h4>{text(row.title)}</h4><p>{note ? "Synced " : "Recorded "}{formatDate(row.at)}{note ? ` · ${text(row.coverage)} · Source time ${formatDate(row.source_timestamp)}` : " · Explicitly saved record"}</p>
        {row.supersedes_journal_id != null && <p>Supersedes an earlier saved record.</p>}
        <Button onClick={() => setSelected(selected === id ? null : id)}>{selected === id ? "Close History Entry" : note ? "Open Note Version" : "Read Saved Record"}</Button>
        {selected === id && (note ? renderReader(row) : <SavedNoteEntry key={id} revision={text(row.note_revision_id)} journal={row} label={personal ? "Conclusion" : "Discussion"} />)}
      </article>;
    })}
    <Button disabled={request.loading} onClick={() => { setCursor(null); setSelected(null); setShowTrades(false); request.refresh(); }}>Reload First History Page</Button>{" "}
    {current && typeof data?.next_cursor === "string" && <Button onClick={() => { setCursor(text(data.next_cursor)); setSelected(null); setShowTrades(false); }}>Older Thinking</Button>}
    {current && typeof data?.instrument_id === "string" && typeof data.window_start === "string" && typeof data.window_end === "string" && <>
      <Button onClick={() => setShowTrades(value => !value)}>{showTrades ? "Hide Period Transactions" : "Show Transactions in This Period"}</Button>
      {showTrades && <HistoryTrades key={`${data.window_start}:${data.window_end}`} revision={revision} start={data.window_start} end={data.window_end} />}
    </>}
  </section>;
}
