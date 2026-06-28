import { describe, expect, it } from "vitest";

import { validatePreviewSupabaseEnv } from "../../scripts/verify-preview-supabase-env";

describe("validatePreviewSupabaseEnv", () => {
  it("fails PR previews that still point at the shared preview Supabase project", () => {
    const result = validatePreviewSupabaseEnv({
      VERCEL_ENV: "preview",
      VERCEL_GIT_PULL_REQUEST_ID: "284",
      NEXT_PUBLIC_SUPABASE_URL: "https://psbteexygkspyotkyflc.supabase.co",
      SUPABASE_URL: "https://psbteexygkspyotkyflc.supabase.co",
    });

    if (result.ok) {
      throw new Error(
        "Expected shared preview Supabase project to fail validation.",
      );
    }
    expect(result.message).toContain("shared preview Supabase project");
    expect(result.message).toContain("psbteexygkspyotkyflc");
  });

  it("passes PR previews that point at a branch Supabase project", () => {
    const result = validatePreviewSupabaseEnv({
      VERCEL_ENV: "preview",
      VERCEL_GIT_PULL_REQUEST_ID: "284",
      NEXT_PUBLIC_SUPABASE_URL: "https://loyzdpdwmpkxpmuionaf.supabase.co",
      SUPABASE_URL: "https://loyzdpdwmpkxpmuionaf.supabase.co",
    });

    expect(result).toEqual({ ok: true });
  });

  it("skips non-PR preview builds", () => {
    const result = validatePreviewSupabaseEnv({
      VERCEL_ENV: "preview",
      NEXT_PUBLIC_SUPABASE_URL: "https://psbteexygkspyotkyflc.supabase.co",
      SUPABASE_URL: "https://psbteexygkspyotkyflc.supabase.co",
    });

    expect(result).toEqual({ ok: true });
  });
});
