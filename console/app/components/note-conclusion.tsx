"use client";

import { useEffect, useRef, useState } from "react";
import { getJson, postApi } from "../lib/api";
import { asObject } from "../lib/coerce";
import { Button, ErrorNote, FormField, Textarea } from "./ui";
import { SavedNoteEntry } from "./note-conversation";

type Dict = Record<string, unknown>;

export function NoteConclusion({ revision, onSaved }: { revision: string; onSaved: () => void }) {
  const storageKey = `note-conclusion-request:${revision}`;
  const [body, setBody] = useState("");
  const [requestId, setRequestId] = useState<string | null>(null);
  const [expectedHash, setExpectedHash] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Dict | null>(null);
  const [readSaved, setReadSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const locked = useRef(false);
  const submitted = useRef<Dict | null>(null);
  const alive = useRef(true);

  async function checkReceipt(id: string) {
    const response = asObject(await getJson(`/api/note-desk/${encodeURIComponent(revision)}/conclusion-receipt?request_id=${encodeURIComponent(id)}`));
    const data = asObject(response.data);
    if (data.note_revision_id !== revision) throw new Error("Mismatched receipt");
    if (data.status !== "RECORDED" || typeof data.journal_id !== "string") return false;
    if (alive.current) {
      setReceipt(data); setMessage(null); setBody("");
      submitted.current = null;
      onSaved();
    }
    return true;
  }

  useEffect(() => {
    alive.current = true;
    let id: string | null = null;
    try {
      const saved = sessionStorage.getItem(storageKey);
      if (saved) {
        const pending = JSON.parse(saved);
        if (typeof pending.id !== "string" || !/^[a-f0-9]{64}$/.test(pending.bodyHash)) throw new Error("Invalid recovery data");
        id = pending.id; setExpectedHash(pending.bodyHash);
      }
    } catch {
      setMessage("The browser recovery record is unavailable. Check saved history before writing another conclusion.");
      return () => { alive.current = false; };
    }

    setRequestId(id);
    if (id) {
      locked.current = true; setBusy(true);
      void checkReceipt(id).then(found => {
        if (!found && alive.current) setMessage("No saved receipt found yet. Re-enter the original conclusion to retry the same request, or check again.");
      }).catch(() => {
        if (alive.current) setMessage("Save outcome is unconfirmed. Check the saved receipt before starting another conclusion.");
      }).finally(() => {
        locked.current = false;
        if (alive.current) { setBusy(false); setReady(true); }
      });
    } else setReady(true);
    return () => { alive.current = false; };
    // The parent keys this editor by exact revision; draft text is never persisted.
  }, [storageKey]);

  async function save() {
    if (locked.current || !body.trim()) return;
    locked.current = true; setBusy(true); setMessage(null);
    const id = requestId ?? `note-conclusion:${crypto.randomUUID()}`;
    try {
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body.trim()));
      const bodyHash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
      if (expectedHash && bodyHash !== expectedHash) {
        setMessage("This differs from the original pending conclusion. Re-enter the original text or check its saved receipt.");
        return;
      }
      // Store only request identity and an equality fingerprint, never private text.
      sessionStorage.setItem(storageKey, JSON.stringify({ id, bodyHash }));
      setRequestId(id); setExpectedHash(bodyHash);
      submitted.current ??= {
        operation: "journal", entry_type: "note", case_id: null,
        title: body.trim().split("\n")[0].slice(0, 120), body_markdown: body.trim(),
        authored_by: "user", confirmed_by: "user", instrument_ids: [],
        topic_tags: ["personal_conclusion_v1"], related_entity_type: "external_note_revision",
        related_entity_id: revision, idempotency_key: id,
      };
      try {
        await postApi("/api/tools/invoke", {
          tool_name: "research_memory_append", confirmation: "research_memory_append",
          arguments: { request: submitted.current }, preserve_full_result: true,
        });
      } catch { /* A lost response is resolved by the read-only exact-key receipt. */ }
      if (!await checkReceipt(id) && alive.current) {
        setMessage("Save is unconfirmed. Check the receipt or retry this exact conclusion; the same request will not create a duplicate.");
      }
    } catch {
      if (alive.current) setMessage("The save receipt could not be verified. Keep this draft and retry the same request.");
    } finally {
      locked.current = false;
      if (alive.current) setBusy(false);
    }
  }

  async function recheck() {
    if (!requestId || locked.current) return;
    locked.current = true; setBusy(true);
    try {
      if (!await checkReceipt(requestId) && alive.current) setMessage("No saved receipt found yet. Retry the same request; do not start a duplicate conclusion.");
    } catch { if (alive.current) setMessage("The saved receipt could not be read. Try checking again."); }
    finally { locked.current = false; if (alive.current) setBusy(false); }
  }

  return <section aria-label="Personal Conclusion">
    <p>Save your own conclusion for this exact note version. No conversation or Research Subject is required. This records a personal note; formal judgments and orders have their own review.</p>
    {message && <ErrorNote>{message}</ErrorNote>}
    {receipt ? <>
      <p role="status">Your conclusion is saved for this note version.</p>
      <Button onClick={() => setReadSaved(value => !value)}>{readSaved ? "Close Saved Conclusion" : "Read Saved Conclusion"}</Button>{" "}
      <Button onClick={() => {
        try { sessionStorage.removeItem(storageKey); } catch { return; }
        setRequestId(null); setExpectedHash(null); setReceipt(null);
        submitted.current = null; setReadSaved(false); setMessage(null);
      }}>Write Another Conclusion</Button>
      {readSaved && <SavedNoteEntry revision={revision} journal={receipt} label="Conclusion" />}
    </> : <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <FormField label="My Conclusion" required><Textarea required maxLength={20000} rows={4} value={body}
        disabled={!ready || busy || submitted.current !== null}
        onChange={event => setBody(event.target.value)} /></FormField>
      <Button type="submit" variant="primary" disabled={!ready || busy || !body.trim()}>{busy ? "Checking Save…" : requestId ? "Retry Same Conclusion" : "Save My Conclusion"}</Button>{" "}
      {requestId && <Button disabled={busy} onClick={() => { void recheck(); }}>Check Saved Receipt</Button>}
    </form>}
  </section>;
}
