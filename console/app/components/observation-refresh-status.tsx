"use client";

import { useEffect, useState } from "react";
import { getJson, postApi } from "../lib/api";
import { ActionButton, Badge, Card, DescriptionList, ErrorNote, LinkButton } from "./ui";

const STORAGE_KEY = "tp.observation-refresh";
const CHANGE_EVENT = "tp-observation-refresh";
type Run = { status: string; attempt: number; result_code: string | null; error_code: string | null };
type Refresh = { request_id: string; run: Run; stages: Record<string, Run | null> };
type Response = { data: Refresh };

export async function startObservationRefresh(requestId = crypto.randomUUID()): Promise<void> {
  window.localStorage.setItem(STORAGE_KEY, requestId);
  window.dispatchEvent(new Event(CHANGE_EVENT));
  const response = await postApi<Response>("/api/observation-refresh", { request_id: requestId });
  window.localStorage.setItem(STORAGE_KEY, response.data.request_id);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function ObservationRefreshStatus() {
  const [requestId, setRequestId] = useState<string | null>(null);
  const [value, setValue] = useState<Refresh | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const restore = () => setRequestId(window.localStorage.getItem(STORAGE_KEY));
    restore();
    window.addEventListener(CHANGE_EVENT, restore);
    window.addEventListener("storage", restore);
    return () => {
      window.removeEventListener(CHANGE_EVENT, restore);
      window.removeEventListener("storage", restore);
    };
  }, []);
  useEffect(() => {
    if (!requestId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const response = await getJson(`/api/observation-refresh/${encodeURIComponent(requestId)}`) as Response;
        if (disposed) return;
        setValue(response.data);
        setError(null);
        if (response.data.run.status === "RUNNING") timer = setTimeout(poll, 2000);
        else {
          const completion = `${requestId}/${response.data.run.attempt}/${response.data.run.status}/${response.data.run.result_code}`;
          if (window.sessionStorage.getItem(`${STORAGE_KEY}.notified`) !== completion) {
            window.sessionStorage.setItem(`${STORAGE_KEY}.notified`, completion);
            window.dispatchEvent(new Event("tp-observation-data-changed"));
          }
        }
      } catch (cause) {
        if (disposed) return;
        setError(cause instanceof Error ? cause.message : "Progress unavailable");
        timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [requestId, busy]);
  if (!requestId) return null;
  const degraded = value?.run.result_code === "OBSERVATION_REFRESH_DEGRADED";
  const retryable = value && ["FAILED", "INTERRUPTED"].includes(value.run.status);
  async function resume() {
    setBusy(true);
    try { await startObservationRefresh(requestId ?? undefined); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Resume failed"); }
    finally { setBusy(false); }
  }
  return <Card title="Observation Refresh" kicker="RUN PROGRESS" action={<LinkButton href="/operations">Open Operations</LinkButton>}>
    <p role="status"><Badge value={degraded ? "DEGRADED" : value?.run.status ?? "LOADING"} />{value ? ` · Attempt ${value.run.attempt}` : ""}</p>
    <p>Capture and save → Interpret → Review. Successful drafts are retained across interruptions.</p>
    {value ? <DescriptionList columns={3} items={Object.entries(value.stages).map(([stage, run]) => ({ label: stage, value: run?.status ?? "NOT_STARTED", detail: run?.error_code ?? (run?.result_code === "OBSERVATION_STAGE_DEGRADED" ? "DEGRADED" : undefined) }))} /> : null}
    {retryable ? <ActionButton busy={busy} onClick={() => void resume()}>Resume Failed Stages</ActionButton> : null}
    {degraded ? <p>Some data or drafts need attention. Review the Notes inbox before requesting another refresh.</p> : null}
    <ErrorNote>{error}</ErrorNote>
  </Card>;
}
