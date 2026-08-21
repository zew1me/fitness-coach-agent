import { createHmac } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

const insertMock = vi.hoisted(() => vi.fn());
const adminClientMock = vi.hoisted(() => ({
  from: vi.fn(() => ({
    insert: insertMock,
  })),
}));

vi.mock(
  "../../lib/supabase-admin",
  (): { getSupabaseAdminClient: () => typeof adminClientMock } => ({
    getSupabaseAdminClient: () => adminClientMock,
  }),
);

async function importRoute(): Promise<
  typeof import("../../app/api/inbound/mailgun/route")
> {
  vi.resetModules();
  return import("../../app/api/inbound/mailgun/route");
}

function sign(timestamp: string, token: string, signingKey: string): string {
  return createHmac("sha256", signingKey)
    .update(timestamp + token)
    .digest("hex");
}

function buildRequest(
  fields: Record<string, string>,
  signingKey = "mailgun-secret",
): Request {
  const timestamp = fields["timestamp"] ?? "1710000000";
  const token = fields["token"] ?? "mailgun-token";
  const form = new FormData();
  form.set("timestamp", timestamp);
  form.set("token", token);
  form.set(
    "signature",
    fields["signature"] ?? sign(timestamp, token, signingKey),
  );

  for (const [key, value] of Object.entries(fields)) {
    form.set(key, value);
  }

  return new Request("https://preview.example.com/api/inbound/mailgun", {
    method: "POST",
    body: form,
  });
}

afterEach(() => {
  adminClientMock.from.mockClear();
  insertMock.mockReset();
  delete process.env["MAILGUN_WEBHOOK_SIGNING_KEY"];
});

describe("POST /api/inbound/mailgun", () => {
  it("stores a signed inbound Mailgun email", async () => {
    process.env["MAILGUN_WEBHOOK_SIGNING_KEY"] = "mailgun-secret";
    insertMock.mockResolvedValueOnce({ error: null });
    const { POST } = await importRoute();

    const response = await POST(
      buildRequest({
        recipient: "Codex-GitHub-20260702@Agents.Example.com",
        sender: "noreply@example.com",
        subject: "Verification code",
        "stripped-text": "Your code is 123456",
        "stripped-html": "<p>Your code is 123456</p>",
        "message-headers": '[["X-Test","ok"]]',
      }),
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ ok: true });
    expect(adminClientMock.from).toHaveBeenCalledWith("agent_emails");
    expect(insertMock).toHaveBeenCalledWith({
      to_address: "codex-github-20260702@agents.example.com",
      from_address: "noreply@example.com",
      subject: "Verification code",
      text_body: "Your code is 123456",
      html_body: "<p>Your code is 123456</p>",
      raw: expect.objectContaining({
        recipient: "Codex-GitHub-20260702@Agents.Example.com",
        sender: "noreply@example.com",
        "message-headers": '[["X-Test","ok"]]',
      }),
    });
    const insertedRow = insertMock.mock.calls[0]?.[0] as {
      raw: Record<string, unknown>;
    };
    expect(insertedRow.raw).not.toHaveProperty("token");
    expect(insertedRow.raw).not.toHaveProperty("signature");
  });

  it("falls back to body fields when stripped fields are absent", async () => {
    process.env["MAILGUN_WEBHOOK_SIGNING_KEY"] = "mailgun-secret";
    insertMock.mockResolvedValueOnce({ error: null });
    const { POST } = await importRoute();

    const response = await POST(
      buildRequest({
        recipient: "codex@agents.example.com",
        "body-plain": "Plain body",
        "body-html": "<p>HTML body</p>",
      }),
    );

    expect(response.status).toBe(200);
    expect(insertMock).toHaveBeenCalledWith(
      expect.objectContaining({
        text_body: "Plain body",
        html_body: "<p>HTML body</p>",
      }),
    );
  });

  it("rejects invalid signatures before inserting", async () => {
    process.env["MAILGUN_WEBHOOK_SIGNING_KEY"] = "mailgun-secret";
    const { POST } = await importRoute();

    const response = await POST(
      buildRequest({
        recipient: "codex@agents.example.com",
        signature: "bad-signature",
      }),
    );

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      error: "invalid_signature",
    });
    expect(adminClientMock.from).not.toHaveBeenCalled();
    expect(insertMock).not.toHaveBeenCalled();
  });

  it("rejects malformed non-form requests before inserting", async () => {
    process.env["MAILGUN_WEBHOOK_SIGNING_KEY"] = "mailgun-secret";
    const { POST } = await importRoute();

    const response = await POST(
      new Request("https://preview.example.com/api/inbound/mailgun", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ recipient: "codex@agents.example.com" }),
      }),
    );

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({
      error: "invalid_request",
      message: "Request body must be form data.",
    });
    expect(adminClientMock.from).not.toHaveBeenCalled();
    expect(insertMock).not.toHaveBeenCalled();
  });

  it("rejects signed payloads without a recipient", async () => {
    process.env["MAILGUN_WEBHOOK_SIGNING_KEY"] = "mailgun-secret";
    const { POST } = await importRoute();

    const response = await POST(buildRequest({ subject: "No recipient" }));

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({
      error: "invalid_payload",
      message: "Mailgun payload is missing recipient.",
    });
    expect(insertMock).not.toHaveBeenCalled();
  });

  it("reports Supabase insert failures", async () => {
    process.env["MAILGUN_WEBHOOK_SIGNING_KEY"] = "mailgun-secret";
    insertMock.mockResolvedValueOnce({
      error: { message: "database unavailable" },
    });
    const { POST } = await importRoute();

    const response = await POST(
      buildRequest({
        recipient: "codex@agents.example.com",
      }),
    );

    expect(response.status).toBe(500);
    await expect(response.json()).resolves.toEqual({
      error: "email_store_failed",
      message: "database unavailable",
    });
  });
});
