import { describe, expect, it, vi } from "vitest";

import { defaultReturnTo, normalizeReturnTo } from "../../lib/auth";
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

describe("createBrowserSupabaseClient", () => {
  it("uses PKCE auth flow for magic-link callbacks", async () => {
    const createClient = vi.fn(() => ({ auth: {} }));

    vi.doMock("@supabase/supabase-js", () => ({
      createClient
    }));

    const { createBrowserSupabaseClient } = await import("../../lib/supabase");

    createBrowserSupabaseClient("https://example.supabase.co", "anon-key");

    expect(createClient).toHaveBeenCalledWith(
      "https://example.supabase.co",
      "anon-key",
      expect.objectContaining({
        auth: expect.objectContaining({
          flowType: "pkce",
          persistSession: true
        })
      })
    );

    vi.doUnmock("@supabase/supabase-js");
  });
});
