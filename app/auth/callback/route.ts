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

async function restoreSignIn(supabase: SupabaseClient): Promise<Session> {
  // Authenticate the cookie-backed user before reusing a stored session. The
  // browser-session endpoint independently validates the access token as well.
  const { data: userData, error: userError } = await supabase.auth.getUser();
  if (userError !== null) throw userError;
  const { data, error } = await supabase.auth.getSession();
  if (error !== null) throw error;
  if (data.session === null)
    throw new Error("Your session expired. Request a new sign-in code.");
  return { ...data.session, user: userData.user };
}

function browserSessionRetryResponse(
  response: NextResponse,
  returnTo: string,
): NextResponse {
  // Preserve the verified Supabase cookies, but never carry the consumed token
  // or backend error details into the retry URL or page. Require a user click
  // rather than redirecting to an automatic retry loop during an outage.
  const query = new URLSearchParams({ retry: "1", return_to: returnTo });
  const retryPath = `/auth/callback?${query.toString()}`.replaceAll(
    "&",
    "&amp;",
  );
  const headers = new Headers(response.headers);
  headers.delete("location");
  headers.set("Content-Type", "text/html; charset=utf-8");
  headers.set(
    "Content-Security-Policy",
    "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  );
  return new NextResponse(
    `<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Finish signing in</title></head><body><main><h1>Finish signing in</h1><p>Your email was verified, but we couldn't finish setting up your browser session. Please try again.</p><a href="${retryPath}">Retry sign-in</a></main></body></html>`,
    { status: 503, headers },
  );
}

async function completeBrowserSession(
  request: NextRequest,
  response: NextResponse,
  session: Session,
  returnTo: string,
): Promise<NextResponse> {
  try {
    await appendBrowserSessionCookie(request, response, session.access_token);
  } catch {
    defaultLogger.error("auth callback: browser session creation failed");
    return browserSessionRetryResponse(response, returnTo);
  }
  defaultLogger.info("auth callback: login complete", {
    user_id: session.user.id,
    return_to: new URL(returnTo, request.url).pathname,
  });
  return response;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get("code");
  const tokenHash = requestUrl.searchParams.get("token_hash");
  const type = requestUrl.searchParams.get("type");
  const retry = requestUrl.searchParams.get("retry") === "1";
  const returnTo = normalizeReturnTo(requestUrl.searchParams.get("return_to"));

  if (!code && !tokenHash && !retry) {
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
    const session = retry
      ? await restoreSignIn(supabase)
      : await verifySignIn(supabase, code, tokenHash);
    return await completeBrowserSession(
      request,
      redirectResponse,
      session,
      returnTo,
    );
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to finish login.";
    return buildLoginRedirectResponse(request, returnTo, message);
  }
}
