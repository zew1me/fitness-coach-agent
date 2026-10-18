import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sentryMock = vi.hoisted(() => ({
  logger: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
const supabaseMock = vi.hoisted(() => ({
  exchangeCodeForSession: vi.fn(),
  createServerSupabaseClient: vi.fn(),
}));

vi.mock("@sentry/nextjs", () => sentryMock);
vi.mock("../../lib/supabase-server", () => ({
  createServerSupabaseClient: supabaseMock.createServerSupabaseClient,
}));

import { GET } from "../../app/auth/callback/route";

const ENV_KEYS = [
  "VERCEL_ENV",
  "APP_BASE_URL",
  "VERCEL_AUTOMATION_BYPASS_SECRET",
] as const;
const originalFetch = globalThis.fetch;
const originalEnv = Object.fromEntries(
  ENV_KEYS.map((key) => [key, process.env[key]]),
);
const fetchMock = vi.fn();

function buildRequest(
  query: string,
  headers: Record<string, string> = {},
): NextRequest {
  return new NextRequest(`https://app.example.com/auth/callback${query}`, {
    headers,
  });
}

function sessionOk(setCookie: string | null): Response {
  return new Response("{}", {
    status: 200,
    headers: setCookie === null ? {} : { "set-cookie": setCookie },
  });
}

function mockExchangeSuccess(): void {
  supabaseMock.exchangeCodeForSession.mockResolvedValue({
    data: { session: { access_token: "tok-123", user: { id: "user-1" } } },
    error: null,
  });
}

function locationOf(response: Response): URL {
  return new URL(response.headers.get("location") ?? "");
}

beforeEach(() => {
  for (const key of ENV_KEYS) delete process.env[key];
  fetchMock.mockReset();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  supabaseMock.exchangeCodeForSession.mockReset();
  supabaseMock.createServerSupabaseClient.mockReset();
  supabaseMock.createServerSupabaseClient.mockReturnValue({
    auth: { exchangeCodeForSession: supabaseMock.exchangeCodeForSession },
  });
  Object.values(sentryMock.logger).forEach((fn) => fn.mockReset());
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  for (const key of ENV_KEYS) {
    const value = originalEnv[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

describe("GET /auth/callback", () => {
  it("redirects to login when the code param is missing", async () => {
    const response = await GET(buildRequest("?return_to=/profile"));

    const location = locationOf(response);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("return_to")).toBe("/profile");
    expect(location.searchParams.get("error")).toBe(
      "Missing auth code from Supabase.",
    );
    expect(supabaseMock.createServerSupabaseClient).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sentryMock.logger.warn).toHaveBeenCalledOnce();
  });

  it("falls back to the default return path for an open-redirect return_to", async () => {
    const response = await GET(buildRequest("?return_to=//evil.example.com"));

    expect(locationOf(response).searchParams.get("return_to")).toBe("/consent");
  });

  it("completes login, forwards the access token, and relays the session cookie", async () => {
    mockExchangeSuccess();
    const cookie = "session=abc; Path=/; HttpOnly; Secure; SameSite=Lax";
    fetchMock.mockResolvedValue(sessionOk(cookie));

    const response = await GET(buildRequest("?code=abc&return_to=/profile"));

    expect(supabaseMock.exchangeCodeForSession).toHaveBeenCalledWith("abc");
    expect(locationOf(response).pathname).toBe("/profile");
    expect(response.headers.get("set-cookie")).toBe(cookie);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://app.example.com/api/oauth/browser-session");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      access_token: "tok-123",
    });
    expect(sentryMock.logger.info).toHaveBeenCalledWith(
      "auth callback: login complete",
      { user_id: "user-1", return_to: "/profile" },
    );
  });

  it("adds no set-cookie header when the session endpoint sets none", async () => {
    mockExchangeSuccess();
    fetchMock.mockResolvedValue(sessionOk(null));

    const response = await GET(buildRequest("?code=abc"));

    expect(locationOf(response).pathname).toBe("/consent");
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("forwards the incoming cookie header and omits it when absent", async () => {
    mockExchangeSuccess();
    fetchMock.mockResolvedValue(sessionOk(null));

    await GET(buildRequest("?code=abc", { cookie: "sb=1" }));
    await GET(buildRequest("?code=abc"));

    const withCookie = (fetchMock.mock.calls[0]?.[1] as RequestInit)
      .headers as Record<string, string>;
    const without = (fetchMock.mock.calls[1]?.[1] as RequestInit)
      .headers as Record<string, string>;
    expect(withCookie["cookie"]).toBe("sb=1");
    expect(withCookie["Content-Type"]).toBe("application/json");
    expect(without).not.toHaveProperty("cookie");
  });

  it("adds the Vercel bypass header only when the secret is configured", async () => {
    mockExchangeSuccess();
    fetchMock.mockResolvedValue(sessionOk(null));

    await GET(buildRequest("?code=abc"));
    process.env["VERCEL_AUTOMATION_BYPASS_SECRET"] = "s3cret";
    await GET(buildRequest("?code=abc"));

    const first = (fetchMock.mock.calls[0]?.[1] as RequestInit)
      .headers as Record<string, string>;
    const second = (fetchMock.mock.calls[1]?.[1] as RequestInit)
      .headers as Record<string, string>;
    expect(first).not.toHaveProperty("x-vercel-protection-bypass");
    expect(second["x-vercel-protection-bypass"]).toBe("s3cret");
  });

  it("prefers APP_BASE_URL outside preview deployments", async () => {
    mockExchangeSuccess();
    fetchMock.mockResolvedValue(sessionOk(null));
    process.env["APP_BASE_URL"] = "https://prod.example.com";

    await GET(buildRequest("?code=abc"));

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://prod.example.com/api/oauth/browser-session",
    );
  });

  it("uses the request origin on preview even when APP_BASE_URL is set", async () => {
    mockExchangeSuccess();
    fetchMock.mockResolvedValue(sessionOk(null));
    process.env["APP_BASE_URL"] = "https://prod.example.com";
    process.env["VERCEL_ENV"] = "preview";

    await GET(buildRequest("?code=abc"));

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://app.example.com/api/oauth/browser-session",
    );
  });

  it("redirects to login with the error when the code exchange fails", async () => {
    supabaseMock.exchangeCodeForSession.mockResolvedValue({
      data: { session: null },
      error: Object.assign(new Error("bad code"), { code: "bad_code" }),
    });

    const response = await GET(buildRequest("?code=abc&return_to=/profile"));

    const location = locationOf(response);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("error")).toBe("bad code");
    expect(location.searchParams.get("return_to")).toBe("/profile");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sentryMock.logger.error).toHaveBeenCalledWith(
      "auth callback: code exchange failed",
      expect.objectContaining({ error_code: "bad_code" }),
    );
  });

  it("reports the status and truncated body when the browser-session call fails", async () => {
    mockExchangeSuccess();
    fetchMock.mockResolvedValue(new Response("x".repeat(500), { status: 401 }));

    const response = await GET(buildRequest("?code=abc"));

    const error = locationOf(response).searchParams.get("error") ?? "";
    expect(locationOf(response).pathname).toBe("/login");
    expect(error).toContain(
      "Unable to establish the OAuth browser session (401: ",
    );
    expect(error).toBe(
      `Unable to establish the OAuth browser session (401: ${"x".repeat(200)})`,
    );
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("omits the body suffix when the failed response has an empty body", async () => {
    mockExchangeSuccess();
    fetchMock.mockResolvedValue(new Response("", { status: 500 }));

    const response = await GET(buildRequest("?code=abc"));

    expect(locationOf(response).searchParams.get("error")).toBe(
      "Unable to establish the OAuth browser session (500)",
    );
  });

  it("uses a generic message when a non-Error value is thrown", async () => {
    mockExchangeSuccess();
    fetchMock.mockRejectedValue("network down");

    const response = await GET(buildRequest("?code=abc"));

    expect(locationOf(response).searchParams.get("error")).toBe(
      "Unable to finish login.",
    );
  });
});
