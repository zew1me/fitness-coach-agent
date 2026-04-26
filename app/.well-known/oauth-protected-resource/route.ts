import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { buildOAuthProtectedResourceMetadata } from "../../../lib/oauth-metadata";

export function GET(request: NextRequest): NextResponse {
  return NextResponse.json(buildOAuthProtectedResourceMetadata(request.nextUrl.origin));
}
