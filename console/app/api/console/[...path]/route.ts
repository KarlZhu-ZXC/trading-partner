import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  LAN_SESSION_COOKIE,
  lanModeEnabled,
  lanPassword,
  verifyLanSession,
} from "../../../lib/lan-auth";

const LOOPBACK_ORIGIN = "http://127.0.0.1:8765";
const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/u;
const REQUEST_HEADERS = ["accept", "content-type", "x-trading-partner-console-token"];
const RESPONSE_HEADERS = ["cache-control", "content-type", "x-accel-buffering"];

type RouteContext = { params: Promise<{ path: string[] }> };

function isLoopbackRequest(request: Request): boolean {
  const hostname = new URL(request.url).hostname.toLowerCase();
  return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "[::1]";
}

async function authorized(request: Request): Promise<boolean> {
  if (!lanModeEnabled()) return isLoopbackRequest(request);
  const password = lanPassword();
  const token = (await cookies()).get(LAN_SESSION_COOKIE)?.value;
  return password !== null && await verifyLanSession(token, password);
}

async function proxyRequest(request: Request, context: RouteContext): Promise<Response> {
  if (!await authorized(request)) {
    return NextResponse.json({ detail: "LAN session required" }, {
      status: 401,
      headers: { "Cache-Control": "no-store" },
    });
  }

  const { path } = await context.params;
  if (path.length === 0 || path.some((segment) => !SAFE_SEGMENT.test(segment) || segment === "..")) {
    return NextResponse.json({ detail: "Invalid Console API path" }, { status: 400 });
  }

  const incomingUrl = new URL(request.url);
  const upstreamPath = path[0] === "api" ? path.join("/") : `api/${path.join("/")}`;
  const target = `${LOOPBACK_ORIGIN}/${upstreamPath}${incomingUrl.search}`;
  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      redirect: "manual",
      cache: "no-store",
    });
    const responseHeaders = new Headers();
    for (const name of RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    responseHeaders.set("Cache-Control", "no-store");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json({ detail: "Local Console API is unavailable" }, {
      status: 502,
      headers: { "Cache-Control": "no-store" },
    });
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const HEAD = proxyRequest;
