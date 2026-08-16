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
let failureWindowStartedAt = 0;
let failuresInWindow = 0;

function noStoreJson(body: unknown, status = 200): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function canAttemptLogin(): boolean {
  const now = Date.now();
  if (now - failureWindowStartedAt >= FAILURE_WINDOW_MS) {
    failureWindowStartedAt = now;
    failuresInWindow = 0;
  }
  return failuresInWindow < FAILURE_LIMIT;
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
  const password = lanPassword();
  if (!password) return noStoreJson({ detail: "LAN mode password is not configured safely" }, 503);
  if (!canAttemptLogin()) {
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
    failuresInWindow += 1;
    return noStoreJson({ detail: "Incorrect password" }, 401);
  }

  failuresInWindow = 0;
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
