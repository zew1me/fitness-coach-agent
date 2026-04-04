import { describe, expect, it, vi } from "vitest";

import { buildLoginRedirectPath, defaultReturnTo, normalizeReturnTo } from "../../lib/auth";
import { siteConfig } from "../../lib/site";

describe("siteConfig", () => {
  it("exposes the app name", () => {
    expect(siteConfig.appName).toContain("Exercise Training Plan GPT");
  });
});

describe("normalizeReturnTo", () => {
  it("keeps same-origin relative paths", () => {
    expect(normalizeReturnTo("/api/oauth/authorize?state=test")).toBe(
      "/api/oauth/authorize?state=test"
    );
  });

  it("falls back for missing or unsafe destinations", () => {
    expect(normalizeReturnTo(null)).toBe(defaultReturnTo);
    expect(normalizeReturnTo("")).toBe(defaultReturnTo);
    expect(normalizeReturnTo("https://example.com")).toBe(defaultReturnTo);
    expect(normalizeReturnTo("//example.com")).toBe(defaultReturnTo);
  });
});

describe("buildLoginRedirectPath", () => {
  it("preserves the normalized destination and optional error message", () => {
    expect(buildLoginRedirectPath("/profile", "Missing auth code from Supabase.")).toBe(
      "/login?return_to=%2Fprofile&error=Missing+auth+code+from+Supabase."
    );
  });

  it("falls back to the default destination for unsafe values", () => {
    expect(buildLoginRedirectPath("https://example.com", null)).toBe(
      `/login?return_to=${encodeURIComponent(defaultReturnTo)}`
    );
  });
});

describe("createBrowserSupabaseClient", () => {
  it("creates the browser client through the Supabase SSR helper", async () => {
    const createBrowserClient = vi.fn(() => ({ auth: {} }));

    vi.doMock("@supabase/ssr", () => ({
      createBrowserClient
    }));

    const { createBrowserSupabaseClient } = await import("../../lib/supabase");

    createBrowserSupabaseClient("https://example.supabase.co", "anon-key");

    expect(createBrowserClient).toHaveBeenCalledWith(
      "https://example.supabase.co",
      "anon-key"
    );

    vi.doUnmock("@supabase/ssr");
  });
});
