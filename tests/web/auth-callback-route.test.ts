import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sentryMock = vi.hoisted(() => ({
  logger: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
const supabaseMock = vi.hoisted(() => ({
  exchangeCodeForSession: vi.fn(),
  verifyOtp: vi.fn(),
  getUser: vi.fn(),
  getSession: vi.fn(),
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
  supabaseMock.verifyOtp.mockReset();
  supabaseMock.getUser.mockReset();
  supabaseMock.getSession.mockReset();
  supabaseMock.createServerSupabaseClient.mockReset();
  supabaseMock.createServerSupabaseClient.mockReturnValue({
    auth: {
      exchangeCodeForSession: supabaseMock.exchangeCodeForSession,
      verifyOtp: supabaseMock.verifyOtp,
      getUser: supabaseMock.getUser,
      getSession: supabaseMock.getSession,
    },
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

  it("verifies an email hash without a PKCE cookie and sets both sessions", async () => {
    supabaseMock.createServerSupabaseClient.mockImplementation(
      (_request, response) => ({
        auth: {
          verifyOtp: supabaseMock.verifyOtp.mockImplementation(() => {
            response.cookies.set("sb-session", "supabase-session", {
              httpOnly: true,
            });
            return Promise.resolve({
              data: {
                session: {
                  access_token: "email-access-token",
                  user: { id: "email-user" },
                },
              },
              error: null,
            });
          }),
        },
      }),
    );
    fetchMock.mockResolvedValue(
      sessionOk("app-session=browser; Path=/; HttpOnly"),
    );
    const response = await GET(
      buildRequest("?token_hash=email-hash&type=email&return_to=%2Fprofile"),
    );
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(supabaseMock.verifyOtp).toHaveBeenCalledWith({
      token_hash: "email-hash",
      type: "email",
    });
    expect(supabaseMock.exchangeCodeForSession).not.toHaveBeenCalled();
    expect(locationOf(response).toString()).toBe(
      "https://app.example.com/profile",
    );
    expect(response.headers.get("set-cookie")).toContain(
      "sb-session=supabase-session",
    );
    expect(response.headers.get("set-cookie")).toContain("app-session=browser");
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual({
      access_token: "email-access-token",
    });
    expect(JSON.stringify(sentryMock.logger.info.mock.calls)).not.toContain(
      "email-hash",
    );
  });

  it.each(["", "recovery", "invite", "signup"])(
    "rejects an unsupported email link type %s before consuming the token",
    async (type) => {
      const response = await GET(
        buildRequest(`?token_hash=email-hash&type=${type}`),
      );
      expect(locationOf(response).pathname).toBe("/login");
      expect(locationOf(response).searchParams.get("error")).toBe(
        "Invalid email sign-in link.",
      );
      expect(supabaseMock.createServerSupabaseClient).not.toHaveBeenCalled();
    },
  );

  it.each(["expired", "already used"])(
    "returns to login on an %s email token without establishing a session",
    async (failure) => {
      supabaseMock.verifyOtp.mockResolvedValue({
        data: { session: null },
        error: Object.assign(new Error(`Token ${failure}`), {
          code: "otp_expired",
        }),
      });
      const response = await GET(
        buildRequest("?token_hash=email-hash&type=email&return_to=%2Fprofile"),
      );
      expect(locationOf(response).pathname).toBe("/login");
      expect(locationOf(response).searchParams.get("return_to")).toBe(
        "/profile",
      );
      expect(fetchMock).not.toHaveBeenCalled();
      expect(response.headers.get("set-cookie")).toBeNull();
      expect(sentryMock.logger.error).toHaveBeenCalledWith(
        "auth callback: email verification failed",
        { error_code: "otp_expired", error_name: "Error" },
      );
    },
  );

  it("rejects email verification without a session", async () => {
    supabaseMock.verifyOtp.mockResolvedValue({
      data: { session: null },
      error: null,
    });
    const response = await GET(
      buildRequest("?token_hash=email-hash&type=email"),
    );
    expect(locationOf(response).pathname).toBe("/login");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    "/\n/evil.example",
  ])(
    "does not redirect email links to an external return_to: %j",
    async (returnTo) => {
      supabaseMock.verifyOtp.mockResolvedValue({
        data: { session: { access_token: "tok", user: { id: "user" } } },
        error: null,
      });
      fetchMock.mockResolvedValue(sessionOk(null));
      const response = await GET(
        buildRequest(
          `?token_hash=email-hash&type=email&return_to=${encodeURIComponent(returnTo)}`,
        ),
      );
      expect(locationOf(response).toString()).toBe(
        "https://app.example.com/consent",
      );
    },
  );

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

  it.each(["http", "network"])(
    "preserves a verified session after a %s failure and retries without the consumed link",
    async (failure) => {
      const user = { id: "email-user" };
      const session = { access_token: "verified-access-token", user };
      supabaseMock.verifyOtp.mockResolvedValue({
        data: { session },
        error: null,
      });
      supabaseMock.getUser.mockResolvedValue({ data: { user }, error: null });
      supabaseMock.getSession.mockResolvedValue({
        data: { session },
        error: null,
      });
      supabaseMock.createServerSupabaseClient.mockImplementation(
        (_request, response) => {
          response.cookies.set("sb-session", "verified-session", {
            httpOnly: true,
            secure: true,
          });
          return {
            auth: {
              verifyOtp: supabaseMock.verifyOtp,
              getUser: supabaseMock.getUser,
              getSession: supabaseMock.getSession,
            },
          };
        },
      );
      if (failure === "http")
        fetchMock.mockResolvedValueOnce(
          new Response("private backend details", { status: 503 }),
        );
      else
        fetchMock.mockRejectedValueOnce(new Error("private network details"));
      const response = await GET(
        buildRequest(
          "?token_hash=consumed-token&type=email&return_to=%2Fprofile",
        ),
      );
      expect(response.status).toBe(503);
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.get("set-cookie")).toContain(
        "sb-session=verified-session",
      );
      expect(response.headers.get("set-cookie")).toContain("HttpOnly");
      expect(response.headers.get("set-cookie")).toContain("Secure");
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(response.headers.get("referrer-policy")).toBe("no-referrer");
      const html = await response.text();
      expect(html).toContain("Retry sign-in");
      expect(html).not.toMatch(
        /consumed-token|verified-access-token|private .* details/,
      );
      const retryPath = html
        .match(/href="([^"]+)"/)?.[1]
        ?.replaceAll("&amp;", "&");
      expect(retryPath).toBe("/auth/callback?retry=1&return_to=%2Fprofile");
      fetchMock.mockResolvedValueOnce(
        sessionOk("coach_browser_session=browser; Path=/; HttpOnly"),
      );
      const retried = await GET(
        new NextRequest(new URL(retryPath ?? "", "https://app.example.com"), {
          headers: { cookie: "sb-session=verified-session" },
        }),
      );
      expect(locationOf(retried).pathname).toBe("/profile");
      expect(retried.headers.get("set-cookie")).toContain(
        "coach_browser_session=browser",
      );
      expect(supabaseMock.verifyOtp).toHaveBeenCalledTimes(1);
      expect(supabaseMock.exchangeCodeForSession).not.toHaveBeenCalled();
      expect(supabaseMock.getUser).toHaveBeenCalledOnce();
      expect(supabaseMock.getSession).toHaveBeenCalledOnce();
      expect(JSON.parse(fetchMock.mock.calls[1]?.[1].body)).toEqual({
        access_token: session.access_token,
      });
    },
  );

  it("also offers recovery after PKCE exchange when the backend fails", async () => {
    mockExchangeSuccess();
    fetchMock.mockRejectedValue("network down");
    const response = await GET(buildRequest("?code=consumed-code"));
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("consumed-code");
  });

  it("rejects a retry without an authenticated Supabase user", async () => {
    supabaseMock.getUser.mockResolvedValue({
      data: { user: null },
      error: new Error("Session expired"),
    });
    const response = await GET(buildRequest("?retry=1&return_to=%2Fprofile"));
    expect(locationOf(response).pathname).toBe("/login");
    expect(locationOf(response).searchParams.get("return_to")).toBe("/profile");
    expect(supabaseMock.getSession).not.toHaveBeenCalled();
    expect(supabaseMock.verifyOtp).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns to login if the retry session has disappeared", async () => {
    supabaseMock.getUser.mockResolvedValue({
      data: { user: { id: "user" } },
      error: null,
    });
    supabaseMock.getSession.mockResolvedValue({
      data: { session: null },
      error: null,
    });
    const response = await GET(buildRequest("?retry=1"));
    expect(locationOf(response).pathname).toBe("/login");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
