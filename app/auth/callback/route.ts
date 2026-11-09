import type { Session, SupabaseClient } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";

import { buildLoginRedirectPath, normalizeReturnTo } from "../../../lib/auth";
import { defaultLogger } from "../../../lib/logger";
import { createServerSupabaseClient } from "../../../lib/supabase-server";

function callbackRedirect(url: URL): NextResponse {
  const response = NextResponse.redirect(url);
  // Email hashes are credentials: do not cache them or forward them as referrers.
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

function buildLoginRedirectResponse(
  request: NextRequest,
  returnTo: string,
  message: string,
): NextResponse {
  return callbackRedirect(
    new URL(buildLoginRedirectPath(returnTo, message), request.url),
  );
}

async function appendBrowserSessionCookie(
  request: NextRequest,
  response: NextResponse,
  accessToken: string,
): Promise<void> {
  // On Vercel preview deployments, always use the request origin so that the
  // internal browser-session call targets the same deployment that served the
  // auth callback.  APP_BASE_URL (if set) may point to the production URL,
  // which would cause JWT validation to fail because the token was issued by
  // the preview Supabase project.
  const isPreview = process.env["VERCEL_ENV"] === "preview";
  const baseUrl = isPreview
    ? request.nextUrl.origin
    : (process.env["APP_BASE_URL"] ?? request.nextUrl.origin);
  const fetchHeaders: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const cookieHeader = request.headers.get("cookie");
  if (cookieHeader !== null) {
    fetchHeaders["cookie"] = cookieHeader;
  }
  const bypassSecret = process.env["VERCEL_AUTOMATION_BYPASS_SECRET"];
  if (bypassSecret) {
    fetchHeaders["x-vercel-protection-bypass"] = bypassSecret;
  }
  const sessionResponse = await fetch(`${baseUrl}/api/oauth/browser-session`, {
    method: "POST",
    headers: fetchHeaders,
    body: JSON.stringify({ access_token: accessToken }),
  });

  if (!sessionResponse.ok) {
    const body = await sessionResponse.text().catch(() => "");
    throw new Error(
      `Unable to establish the OAuth browser session (${sessionResponse.status}${body ? `: ${body.slice(0, 200)}` : ""})`,
    );
  }

  const setCookieHeader = sessionResponse.headers.get("set-cookie");
  if (setCookieHeader !== null) {
    response.headers.append("set-cookie", setCookieHeader);
  }
}

async function verifySignIn(
  supabase: SupabaseClient,
  code: string | null,
  tokenHash: string | null,
): Promise<Session> {
  // Email is requested server-side without a browser PKCE verifier. Verify
  // the token hash directly so the link also works in a different browser.
  // Keep code exchange for existing PKCE callers.
  const { data, error } = tokenHash
    ? await supabase.auth.verifyOtp({ token_hash: tokenHash, type: "email" })
    : await supabase.auth.exchangeCodeForSession(code ?? "");
  if (error !== null) {
    defaultLogger.error(
      tokenHash
        ? "auth callback: email verification failed"
        : "auth callback: code exchange failed",
      { error_code: error.code ?? "unknown", error_name: error.name },
    );
    throw error;
  }
  if (data.session === null) {
    throw new Error("No session returned after email sign-in.");
  }
  return data.session;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get("code");
  const tokenHash = requestUrl.searchParams.get("token_hash");
  const type = requestUrl.searchParams.get("type");
  const returnTo = normalizeReturnTo(requestUrl.searchParams.get("return_to"));

  if (!code && !tokenHash) {
    defaultLogger.warn("auth callback: missing code param");
    return buildLoginRedirectResponse(
      request,
      returnTo,
      "Missing auth code from Supabase.",
    );
  }

  // Only email sign-in is supported here; never accept recovery/invite types
  // supplied by a URL or silently fall back to another credential on failure.
  if (tokenHash && type !== "email") {
    return buildLoginRedirectResponse(
      request,
      returnTo,
      "Invalid email sign-in link.",
    );
  }

  try {
    const redirectResponse = callbackRedirect(new URL(returnTo, request.url));
    const supabase = createServerSupabaseClient(request, redirectResponse);
    const session = await verifySignIn(supabase, code, tokenHash);

    await appendBrowserSessionCookie(
      request,
      redirectResponse,
      session.access_token,
    );
    defaultLogger.info("auth callback: login complete", {
      user_id: session.user.id,
      return_to: new URL(returnTo, request.url).pathname,
    });
    return redirectResponse;
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to finish login.";
    return buildLoginRedirectResponse(request, returnTo, message);
  }
}
