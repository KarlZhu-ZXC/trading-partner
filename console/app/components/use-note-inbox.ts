"use client";

import { useEffect, useState } from "react";
import { sendJsonMethod, useApi } from "../lib/api";

type Payload = { data: Record<string, unknown> };

/** Search terms remain in a read-only request body, never the URL or browser storage. */
export function useNoteInbox(query: string, offset: number) {
  const browse = useApi<Payload>(offset ? `/api/note-desk?offset=${offset}` : "/api/note-desk", { enabled: !query });
  const [nonce, setNonce] = useState(0);
  const key = JSON.stringify([query, offset, nonce]);
  const [search, setSearch] = useState<{ key: string; data: Payload | null; error: string | null } | null>(null);
  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    sendJsonMethod("/api/note-desk/search", "POST", { query, offset }, controller.signal)
      .then(value => { if (!controller.signal.aborted) setSearch({ key, data: value as Payload, error: null }); })
      .catch(() => { if (!controller.signal.aborted) setSearch({ key, data: null, error: "Saved notes could not be searched." }); });
    return () => controller.abort();
  }, [query, offset, key]);
  if (!query) return { ...browse, data: (browse.data?.data?.offset ?? 0) === offset ? browse.data : null };
  return {
    data: search?.key === key ? search.data : null,
    loading: search?.key !== key,
    error: search?.key === key ? search.error : null,
    refresh: () => setNonce(value => value + 1),
  };
}
