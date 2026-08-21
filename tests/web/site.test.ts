import { describe, expect, it } from "vitest";

import {
  buildLoginRedirectPath,
  defaultReturnTo,
  normalizeReturnTo,
} from "../../lib/auth";
import { buildTavilyMcpUrl, siteConfig } from "../../lib/site";

describe("siteConfig", () => {
  it("exposes the app name", () => {
    expect(siteConfig.appName).toBe("Coach Arden");
    expect(siteConfig.description).toBe(
      "AI Chat Bot for endurance athletes that helps plan, adapt, and review training using athlete profile and workout data.",
    );
  });
});

describe("buildTavilyMcpUrl", () => {
  it("appends tavilyApiKey as a query param", () => {
    expect(buildTavilyMcpUrl("my-key")).toBe(
      "https://mcp.tavily.com/mcp/?tavilyApiKey=my-key",
    );
  });

  it("does not bake the key into the base URL constant", () => {
    // The static base URL should never contain a real key prefix
    expect(buildTavilyMcpUrl("x")).toContain("mcp.tavily.com");
    expect(buildTavilyMcpUrl("x")).toContain("?tavilyApiKey=");
  });
});

describe("normalizeReturnTo", () => {
  it("keeps same-origin relative paths", () => {
    expect(normalizeReturnTo("/api/oauth/authorize?state=test")).toBe(
      "/api/oauth/authorize?state=test",
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
    expect(
      buildLoginRedirectPath("/profile", "Missing auth code from Supabase."),
    ).toBe(
      "/login?return_to=%2Fprofile&error=Missing+auth+code+from+Supabase.",
    );
  });

  it("falls back to the default destination for unsafe values", () => {
    expect(buildLoginRedirectPath("https://example.com", null)).toBe(
      `/login?return_to=${encodeURIComponent(defaultReturnTo)}`,
    );
  });
});
