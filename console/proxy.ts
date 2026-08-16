import { NextRequest, NextResponse } from "next/server";

import {
  LAN_SESSION_COOKIE,
  lanModeEnabled,
  lanPassword,
  verifyLanSession,
} from "./app/lib/lan-auth";

const PUBLIC_PATHS = ["/lan-login", "/api/lan-auth"];

export async function proxy(request: NextRequest): Promise<NextResponse> {
  if (!lanModeEnabled()) return NextResponse.next();

  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))) {
    return NextResponse.next();
  }

  const password = lanPassword();
  const authenticated = password !== null && await verifyLanSession(
    request.cookies.get(LAN_SESSION_COOKIE)?.value,
    password,
  );
  if (authenticated) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "LAN session required" }, {
      status: 401,
      headers: { "Cache-Control": "no-store" },
    });
  }

  const login = request.nextUrl.clone();
  login.pathname = "/lan-login";
  login.search = "";
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/|assets/|favicon.ico).*)"],
};
