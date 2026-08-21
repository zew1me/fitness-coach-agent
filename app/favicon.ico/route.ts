import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

export function GET(request: NextRequest): NextResponse {
  return NextResponse.redirect(
    new URL("/brand/coach-arden-logo.svg", request.url),
    { status: 302 },
  );
}
