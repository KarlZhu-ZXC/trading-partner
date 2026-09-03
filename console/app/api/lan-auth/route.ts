import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  createLanSession,
  LAN_SESSION_COOKIE,
  LAN_SESSION_SECONDS,
  lanModeEnabled,
  lanPassword,
  passwordMatches,
  verifyLanSession,
} from "../../lib/lan-auth";

const FAILURE_LIMIT = 10;
const FAILURE_WINDOW_MS = 60_000;
const FAILURE_BUCKET_LIMIT = 128;
type FailureBucket = { startedAt: number; failures: number };
const failureBuckets = new Map<string, FailureBucket>();

function noStoreJson(body: unknown, status = 200): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function clientKey(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",", 1)[0]?.trim();
  return forwarded && /^[0-9A-Fa-f:.]{2,64}$/u.test(forwarded) ? forwarded : "direct";
}

function failureBucket(key: string): FailureBucket {
  const now = Date.now();
  const current = failureBuckets.get(key);
  if (!current || now - current.startedAt >= FAILURE_WINDOW_MS) {
    const next = { startedAt: now, failures: 0 };
    failureBuckets.set(key, next);
    for (const [candidate, bucket] of failureBuckets) {
      if (now - bucket.startedAt >= FAILURE_WINDOW_MS) failureBuckets.delete(candidate);
    }
    while (failureBuckets.size > FAILURE_BUCKET_LIMIT) {
      const oldest = failureBuckets.keys().next().value as string | undefined;
      if (!oldest) break;
      failureBuckets.delete(oldest);
    }
    return next;
  }
  return current;
}

function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).origin === new URL(request.url).origin;
  } catch {
    return false;
  }
}

export async function GET(): Promise<NextResponse> {
  const enabled = lanModeEnabled();
  const password = lanPassword();
  const token = (await cookies()).get(LAN_SESSION_COOKIE)?.value;
  return noStoreJson({
    enabled,
    configured: !enabled || password !== null,
    authenticated: !enabled || (password !== null && await verifyLanSession(token, password)),
  });
}

export async function POST(request: Request): Promise<NextResponse> {
  if (!lanModeEnabled()) return noStoreJson({ detail: "LAN mode is not enabled" }, 404);
  if (!isSameOrigin(request)) return noStoreJson({ detail: "LAN login origin is not allowed" }, 403);
  const password = lanPassword();
  if (!password) return noStoreJson({ detail: "LAN mode password is not configured safely" }, 503);
  const key = clientKey(request);
  const bucket = failureBucket(key);
  if (bucket.failures >= FAILURE_LIMIT) {
    return noStoreJson({ detail: "Too many attempts. Try again in one minute." }, 429);
  }

  let body: { password?: unknown };
  try {
    body = await request.json() as { password?: unknown };
  } catch {
    return noStoreJson({ detail: "Expected a JSON password" }, 400);
  }
  const candidate = typeof body.password === "string" ? body.password.slice(0, 512) : "";

  if (!await passwordMatches(candidate, password)) {
    bucket.failures += 1;
    return noStoreJson({ detail: "Incorrect password" }, 401);
  }

  failureBuckets.delete(key);
  const response = noStoreJson({ authenticated: true });
  response.cookies.set({
    name: LAN_SESSION_COOKIE,
    value: await createLanSession(password),
    httpOnly: true,
    sameSite: "strict",
    secure: false,
    path: "/",
    maxAge: LAN_SESSION_SECONDS,
  });
  return response;
}

export async function DELETE(): Promise<NextResponse> {
  const response = noStoreJson({ authenticated: false });
  response.cookies.set({
    name: LAN_SESSION_COOKIE,
    value: "",
    httpOnly: true,
    sameSite: "strict",
    secure: false,
    path: "/",
    maxAge: 0,
  });
  return response;
}
