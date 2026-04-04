import { NextRequest, NextResponse } from "next/server";

import { buildLoginRedirectPath, normalizeReturnTo } from "../../../lib/auth";
import { createServerSupabaseClient } from "../../../lib/supabase-server";

function buildLoginRedirectResponse(
  request: NextRequest,
  returnTo: string,
  message: string
): NextResponse {
  return NextResponse.redirect(new URL(buildLoginRedirectPath(returnTo, message), request.url));
}

async function appendBrowserSessionCookie(
  request: NextRequest,
  response: NextResponse,
  accessToken: string
): Promise<void> {
  const baseUrl = process.env["APP_BASE_URL"] ?? request.nextUrl.origin;
  const sessionResponse = await fetch(`${baseUrl}/api/oauth/browser-session`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ access_token: accessToken })
  });

  if (!sessionResponse.ok) {
    throw new Error("Unable to establish the OAuth browser session.");
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
    return buildLoginRedirectResponse(request, returnTo, "Missing auth code from Supabase.");
  }

  try {
    const redirectResponse = NextResponse.redirect(new URL(returnTo, request.url));
    const supabase = createServerSupabaseClient(request, redirectResponse);
    const { data, error } = await supabase.auth.exchangeCodeForSession(code);

    if (error !== null) {
      throw error;
    }

    await appendBrowserSessionCookie(request, redirectResponse, data.session.access_token);
    return redirectResponse;
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to finish login.";
    return buildLoginRedirectResponse(request, returnTo, message);
  }
}
