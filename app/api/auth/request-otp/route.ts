import { createHash, timingSafeEqual } from "node:crypto";

import { NextResponse } from "next/server";

import { normalizeReturnTo } from "../../../../lib/auth";
import { getSupabaseAdminClient } from "../../../../lib/supabase-admin";

type RequestOtpBody = {
  email?: unknown;
  inviteCode?: unknown;
  returnTo?: unknown;
};

type OtpResult = {
  error: { message?: string } | null;
};

type ParsedRequest =
  | {
      body: RequestOtpBody;
      email: string;
      emailRedirectTo: string;
    }
  | NextResponse;

function jsonResponse(body: object, status: number): NextResponse {
  return NextResponse.json(body, { status });
}

function normalizeEmail(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  return normalized.length > 0 ? normalized : null;
}

function normalizeInviteCode(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

function buildEmailRedirectTo(request: Request, returnTo: unknown): string {
  const callbackUrl = new URL("/auth/callback", request.url);
  callbackUrl.searchParams.set(
    "return_to",
    normalizeReturnTo(typeof returnTo === "string" ? returnTo : null)
  );
  return callbackUrl.toString();
}

function hashInviteCode(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

function isInviteCodeValid(candidate: string): boolean {
  const inviteCode = process.env["INVITE_CODE"];
  if (!inviteCode) {
    return false;
  }

  return timingSafeEqual(hashInviteCode(inviteCode), hashInviteCode(candidate));
}

function looksLikeNewUserError(error: { message?: string } | null): boolean {
  if (error === null) {
    return false;
  }

  const message = error.message?.toLowerCase() ?? "";
  return (
    message.includes("user not found") ||
    message.includes("user does not exist") ||
    message.includes("signup") ||
    message.includes("signups")
  );
}

async function sendOtp(email: string, emailRedirectTo: string): Promise<OtpResult> {
  const supabase = getSupabaseAdminClient();
  return supabase.auth.signInWithOtp({
    email,
    options: {
      emailRedirectTo,
      shouldCreateUser: false
    }
  });
}

async function parseRequest(request: Request): Promise<ParsedRequest> {
  let body: RequestOtpBody;
  try {
    body = (await request.json()) as RequestOtpBody;
  } catch {
    return jsonResponse({ error: "invalid_request", message: "Request body must be JSON." }, 400);
  }

  const email = normalizeEmail(body.email);
  if (email === null) {
    return jsonResponse({ error: "invalid_email", message: "Enter a valid email address." }, 400);
  }

  return {
    body,
    email,
    emailRedirectTo: buildEmailRedirectTo(request, body.returnTo)
  };
}

function inviteRequiredResponse(): NextResponse {
  return jsonResponse(
    { error: "invite_required", message: "This looks new. Enter your invite code." },
    409
  );
}

function invalidInviteResponse(): NextResponse {
  return jsonResponse(
    { error: "invalid_invite_code", message: "That invite code is not valid." },
    403
  );
}

function otpSentResponse(): NextResponse {
  return jsonResponse({ status: "otp_sent", inviteRequired: false }, 200);
}

function otpFailureResponse(error: { message?: string } | null): NextResponse {
  return jsonResponse(
    {
      error: "otp_send_failed",
      message: error?.message || "Unable to send a login code."
    },
    502
  );
}

async function createInvitedUser(email: string): Promise<NextResponse | null> {
  const supabase = getSupabaseAdminClient();
  const createResult = await supabase.auth.admin.createUser({
    email,
    email_confirm: true
  });

  if (createResult.error === null) {
    return null;
  }

  return jsonResponse(
    {
      error: "user_create_failed",
      message: createResult.error.message || "Unable to create your account."
    },
    502
  );
}

async function handleNewUser(body: RequestOtpBody, email: string, emailRedirectTo: string): Promise<NextResponse> {
  const inviteCode = normalizeInviteCode(body.inviteCode);
  if (inviteCode === null) {
    return inviteRequiredResponse();
  }

  if (!isInviteCodeValid(inviteCode)) {
    return invalidInviteResponse();
  }

  const createErrorResponse = await createInvitedUser(email);
  if (createErrorResponse !== null) {
    return createErrorResponse;
  }

  const invitedOtpResult = await sendOtp(email, emailRedirectTo);
  return invitedOtpResult.error === null ? otpSentResponse() : otpFailureResponse(invitedOtpResult.error);
}

export async function POST(request: Request): Promise<NextResponse> {
  const parsedRequest = await parseRequest(request);
  if (parsedRequest instanceof NextResponse) {
    return parsedRequest;
  }

  const { body, email, emailRedirectTo } = parsedRequest;
  const initialOtpResult = await sendOtp(email, emailRedirectTo);
  if (initialOtpResult.error === null) {
    return otpSentResponse();
  }

  if (!looksLikeNewUserError(initialOtpResult.error)) {
    return otpFailureResponse(initialOtpResult.error);
  }

  return handleNewUser(body, email, emailRedirectTo);
}
