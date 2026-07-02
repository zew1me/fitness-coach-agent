import { createHmac, timingSafeEqual } from "node:crypto";

import { NextResponse } from "next/server";

import { getSupabaseAdminClient } from "../../../../lib/supabase-admin";

export const runtime = "nodejs";

type AgentEmailInsert = {
  to_address: string;
  from_address: string | null;
  subject: string | null;
  text_body: string | null;
  html_body: string | null;
  raw: Record<string, unknown>;
};

function jsonResponse(body: object, status: number): NextResponse {
  return NextResponse.json(body, { status });
}

function formString(form: FormData, key: string): string {
  const value = form.get(key);
  return typeof value === "string" ? value : "";
}

function optionalFormString(form: FormData, key: string): string | null {
  const value = formString(form, key).trim();
  return value.length > 0 ? value : null;
}

function serializeFormValue(value: FormDataEntryValue): unknown {
  if (typeof value === "string") {
    return value;
  }

  return {
    name: value.name,
    size: value.size,
    type: value.type,
  };
}

function serializeForm(form: FormData): Record<string, unknown> {
  const raw: Record<string, unknown> = {};
  for (const [key, value] of form.entries()) {
    if (key === "token" || key === "signature") {
      continue;
    }

    const serialized = serializeFormValue(value);
    if (Object.prototype.hasOwnProperty.call(raw, key)) {
      const current = raw[key];
      raw[key] = Array.isArray(current)
        ? [...current, serialized]
        : [current, serialized];
      continue;
    }
    raw[key] = serialized;
  }
  return raw;
}

function timingSafeHexEqual(expectedHex: string, actualHex: string): boolean {
  if (!/^[a-f0-9]+$/i.test(actualHex)) {
    return false;
  }

  const expected = Buffer.from(expectedHex, "hex");
  const actual = Buffer.from(actualHex, "hex");
  if (expected.length !== actual.length) {
    return false;
  }
  return timingSafeEqual(expected, actual);
}

function verifyMailgunSignature(form: FormData): boolean {
  const signingKey = process.env["MAILGUN_WEBHOOK_SIGNING_KEY"];
  if (!signingKey) {
    return false;
  }

  const timestamp = formString(form, "timestamp");
  const token = formString(form, "token");
  const signature = formString(form, "signature");
  if (!timestamp || !token || !signature) {
    return false;
  }

  const expectedSignature = createHmac("sha256", signingKey)
    .update(timestamp + token)
    .digest("hex");
  return timingSafeHexEqual(expectedSignature, signature);
}

function buildEmailInsert(form: FormData): AgentEmailInsert | NextResponse {
  const toAddress = optionalFormString(form, "recipient")?.toLowerCase();
  if (!toAddress) {
    return jsonResponse(
      {
        error: "invalid_payload",
        message: "Mailgun payload is missing recipient.",
      },
      400,
    );
  }

  return {
    to_address: toAddress,
    from_address: optionalFormString(form, "sender"),
    subject: optionalFormString(form, "subject"),
    text_body:
      optionalFormString(form, "stripped-text") ??
      optionalFormString(form, "body-plain"),
    html_body:
      optionalFormString(form, "stripped-html") ??
      optionalFormString(form, "body-html"),
    raw: serializeForm(form),
  };
}

export async function POST(request: Request): Promise<NextResponse> {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return jsonResponse(
      { error: "invalid_request", message: "Request body must be form data." },
      400,
    );
  }

  if (!verifyMailgunSignature(form)) {
    return jsonResponse({ error: "invalid_signature" }, 401);
  }

  const row = buildEmailInsert(form);
  if (row instanceof NextResponse) {
    return row;
  }

  const { error } = await getSupabaseAdminClient()
    .from("agent_emails")
    .insert(row);
  if (error) {
    return jsonResponse(
      {
        error: "email_store_failed",
        message: error.message || "Unable to store inbound email.",
      },
      500,
    );
  }

  return jsonResponse({ ok: true }, 200);
}
