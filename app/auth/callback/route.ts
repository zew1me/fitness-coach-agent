import * as Sentry from "@sentry/nextjs";
import { NextRequest, NextResponse } from "next/server";

import { buildLoginRedirectPath, normalizeReturnTo } from "../../../lib/auth";
import { createServerSupabaseClient } from "../../../lib/supabase-server";

function buildLoginRedirectResponse(
  request: NextRequest,
  returnTo: string,
  message: string,
): NextResponse {
  return NextResponse.redirect(
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

export async function GET(request: NextRequest): Promise<NextResponse> {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get("code");
  const returnTo = normalizeReturnTo(requestUrl.searchParams.get("return_to"));

  if (code === null) {
    Sentry.logger.warn("auth callback: missing code param");
    return buildLoginRedirectResponse(
      request,
      returnTo,
      "Missing auth code from Supabase.",
    );
  }

  try {
    const redirectResponse = NextResponse.redirect(
      new URL(returnTo, request.url),
    );
    const supabase = createServerSupabaseClient(request, redirectResponse);
    const { data, error } = await supabase.auth.exchangeCodeForSession(code);

    if (error !== null) {
      Sentry.logger.error("auth callback: code exchange failed", {
        error_code: error.code ?? "unknown",
        error_name: error.name,
      });
      throw error;
    }

    await appendBrowserSessionCookie(
      request,
      redirectResponse,
      data.session.access_token,
    );
    Sentry.logger.info("auth callback: login complete", {
      user_id: data.session.user.id,
      return_to: new URL(returnTo, request.url).pathname,
    });
    return redirectResponse;
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to finish login.";
    return buildLoginRedirectResponse(request, returnTo, message);
  }
}
