import { afterEach, describe, expect, it, vi } from "vitest";

const adminClientMock = vi.hoisted(() => ({
  auth: {
    admin: {
      createUser: vi.fn()
    },
    signInWithOtp: vi.fn()
  }
}));

vi.mock("../../lib/supabase-admin", (): { getSupabaseAdminClient: () => typeof adminClientMock } => ({
  getSupabaseAdminClient: () => adminClientMock
}));

async function importRoute(): Promise<typeof import("../../app/api/auth/request-otp/route")> {
  vi.resetModules();
  return import("../../app/api/auth/request-otp/route");
}

function buildRequest(body: unknown): Request {
  return new Request("https://preview.example.com/api/auth/request-otp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body)
  });
}

afterEach(() => {
  adminClientMock.auth.admin.createUser.mockReset();
  adminClientMock.auth.signInWithOtp.mockReset();
  delete process.env["INVITE_CODE"];
});

describe("POST /api/auth/request-otp", () => {
  it("sends an OTP to an existing user without requiring an invite code", async () => {
    adminClientMock.auth.signInWithOtp.mockResolvedValueOnce({ error: null });
    const { POST } = await importRoute();

    const response = await POST(buildRequest({ email: "Athlete@Example.com", returnTo: "/profile" }));

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      status: "otp_sent",
      inviteRequired: false
    });
    expect(adminClientMock.auth.admin.createUser).not.toHaveBeenCalled();
    expect(adminClientMock.auth.signInWithOtp).toHaveBeenCalledWith({
      email: "athlete@example.com",
      options: {
        emailRedirectTo: "https://preview.example.com/auth/callback?return_to=%2Fprofile",
        shouldCreateUser: false
      }
    });
  });

  it("asks for an invite code before creating a new user", async () => {
    adminClientMock.auth.signInWithOtp.mockResolvedValueOnce({
      error: new Error("User not found")
    });
    const { POST } = await importRoute();

    const response = await POST(buildRequest({ email: "new@example.com" }));

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({
      error: "invite_required",
      message: "This looks new. Enter your invite code."
    });
    expect(adminClientMock.auth.admin.createUser).not.toHaveBeenCalled();
  });

  it("asks for an invite code when the client submits null before the invite field is shown", async () => {
    adminClientMock.auth.signInWithOtp.mockResolvedValueOnce({
      error: new Error("Signups not allowed")
    });
    const { POST } = await importRoute();

    const response = await POST(buildRequest({ email: "new@example.com", inviteCode: null }));

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({
      error: "invite_required",
      message: "This looks new. Enter your invite code."
    });
    expect(adminClientMock.auth.admin.createUser).not.toHaveBeenCalled();
  });

  it("rejects an invalid invite code for a new user", async () => {
    process.env["INVITE_CODE"] = "alpha-access";
    adminClientMock.auth.signInWithOtp.mockResolvedValueOnce({
      error: new Error("Signups not allowed")
    });
    const { POST } = await importRoute();

    const response = await POST(
      buildRequest({ email: "new@example.com", inviteCode: "wrong-code" })
    );

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({
      error: "invalid_invite_code",
      message: "That invite code is not valid."
    });
    expect(adminClientMock.auth.admin.createUser).not.toHaveBeenCalled();
  });

  it("creates a new invited user and then sends an OTP", async () => {
    process.env["INVITE_CODE"] = "alpha-access";
    adminClientMock.auth.signInWithOtp
      .mockResolvedValueOnce({ error: new Error("Signups not allowed") })
      .mockResolvedValueOnce({ error: null });
    adminClientMock.auth.admin.createUser.mockResolvedValueOnce({
      data: { user: { id: "user-1" } },
      error: null
    });
    const { POST } = await importRoute();

    const response = await POST(
      buildRequest({ email: "New@Example.com", inviteCode: "alpha-access", returnTo: "/consent" })
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      status: "otp_sent",
      inviteRequired: false
    });
    expect(adminClientMock.auth.admin.createUser).toHaveBeenCalledWith({
      email: "new@example.com",
      email_confirm: true
    });
    expect(adminClientMock.auth.signInWithOtp).toHaveBeenLastCalledWith({
      email: "new@example.com",
      options: {
        emailRedirectTo: "https://preview.example.com/auth/callback?return_to=%2Fconsent",
        shouldCreateUser: false
      }
    });
  });
});
