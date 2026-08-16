"use client";

import { useCallback, useEffect, useState } from "react";

export const API_BASE =
  process.env.NEXT_PUBLIC_TRADING_PARTNER_API ?? "/api/console";

const CONSOLE_TOKEN_HEADER = "X-Trading-Partner-Console-Token";
let sessionTokenPromise: Promise<string> | null = null;

async function consoleSessionToken(): Promise<string> {
  if (sessionTokenPromise === null) {
    sessionTokenPromise = fetch(`${API_BASE}/api/session`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = (await response.json()) as { token?: unknown };
        if (typeof payload.token !== "string" || payload.token.length < 32) {
          throw new Error("Local Console returned an invalid session token");
        }
        return payload.token;
      })
      .catch((error: unknown) => {
        sessionTokenPromise = null;
        throw error;
      });
  }
  return sessionTokenPromise;
}

export type ApiResult<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  refreshedAt: Date | null;
  refresh: () => void;
};

export function useApi<T>(route: string): ApiResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}${route}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as T;
      })
      .then((value) => {
        setData(value);
        setRefreshedAt(new Date());
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "Unable to connect to the local API");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [route, nonce]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    setNonce((value) => value + 1);
  }, []);
  return { data, loading, error, refreshedAt, refresh };
}

export function envelopeData<T>(value: unknown): T | null {
  if (!value || typeof value !== "object" || !("data" in value)) return null;
  return (value as { data: T }).data;
}

export function listOf<T>(value: unknown, key: string): T[] {
  if (!value || typeof value !== "object") return [];
  const candidate = (value as Record<string, unknown>)[key];
  return Array.isArray(candidate) ? (candidate as T[]) : [];
}

/**
 * Fetch a Console API route with the current loopback session token.
 *
 * The token is intentionally obtained lazily so ordinary server-rendered
 * pages do not need to know about the Console write boundary.  A 403 resets
 * the cached token and retries once, which covers a local API restart without
 * turning a stale session into an infinite retry loop.
 */
export async function authenticatedFetch(
  route: string,
  init: RequestInit = {},
): Promise<Response> {
  const target = /^https?:\/\//i.test(route) ? route : `${API_BASE}${route}`;

  async function send(token: string): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set(CONSOLE_TOKEN_HEADER, token);
    return fetch(target, {
      ...init,
      headers,
    });
  }

  let response = await send(await consoleSessionToken());
  if (response.status === 403) {
    sessionTokenPromise = null;
    response = await send(await consoleSessionToken());
  }
  return response;
}

export async function postApi<T>(route: string, body: unknown): Promise<T> {
  const response = await authenticatedFetch(route, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = (await response.json()) as T & { detail?: string };
  if (!response.ok) {
    throw new Error(payload.detail ?? `HTTP ${response.status}`);
  }
  return payload;
}
