"use client";

import { useCallback, useEffect, useState } from "react";

export const API_BASE =
  process.env.NEXT_PUBLIC_TRADING_PARTNER_API ?? "http://127.0.0.1:8765";

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
        setError(cause instanceof Error ? cause.message : "无法连接本地 API");
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

export async function postApi<T>(route: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${route}`, {
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
