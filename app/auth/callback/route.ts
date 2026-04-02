import { NextRequest, NextResponse } from "next/server";

import { buildLoginRedirectPath, normalizeReturnTo } from "../../../lib/auth";
import { createServerSupabaseClient } from "../../../lib/supabase-server";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get("code");
  const returnTo = normalizeReturnTo(requestUrl.searchParams.get("return_to"));

  if (code === null) {
    return NextResponse.redirect(
      new URL(buildLoginRedirectPath(returnTo, "Missing auth code from Supabase."), request.url)
    );
  }

  try {
    const supabase = await createServerSupabaseClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);

    if (error !== null) {
      throw error;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to finish login.";
    return NextResponse.redirect(new URL(buildLoginRedirectPath(returnTo, message), request.url));
  }

  return NextResponse.redirect(new URL(returnTo, request.url));
}
