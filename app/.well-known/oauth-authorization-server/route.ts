import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { buildOAuthAuthorizationMetadata } from "../../../lib/oauth-metadata";

export function GET(request: NextRequest): NextResponse {
  return NextResponse.json(buildOAuthAuthorizationMetadata(request.nextUrl.origin));
}
