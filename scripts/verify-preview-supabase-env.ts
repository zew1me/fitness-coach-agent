export type PreviewSupabaseEnv = Record<string, string | undefined>;

export type PreviewSupabaseEnvValidation =
  | { ok: true }
  | { ok: false; message: string };

const SHARED_PREVIEW_SUPABASE_REF = "psbteexygkspyotkyflc";

function isPullRequestPreview(env: PreviewSupabaseEnv): boolean {
  return (
    env["VERCEL_ENV"] === "preview" &&
    Boolean(env["VERCEL_GIT_PULL_REQUEST_ID"])
  );
}

function supabaseRefFromUrl(value: string | undefined): string | null {
  if (!value) {
    return null;
  }
  try {
    const hostname = new URL(value).hostname;
    const suffix = ".supabase.co";
    return hostname.endsWith(suffix) ? hostname.slice(0, -suffix.length) : null;
  } catch {
    return null;
  }
}

export function validatePreviewSupabaseEnv(
  env: PreviewSupabaseEnv,
): PreviewSupabaseEnvValidation {
  if (!isPullRequestPreview(env)) {
    return { ok: true };
  }

  const supabaseRefs = [
    [
      "NEXT_PUBLIC_SUPABASE_URL",
      supabaseRefFromUrl(env["NEXT_PUBLIC_SUPABASE_URL"]),
    ],
    ["SUPABASE_URL", supabaseRefFromUrl(env["SUPABASE_URL"])],
  ];
  const sharedPreviewKeys = supabaseRefs
    .filter(([, ref]) => ref === SHARED_PREVIEW_SUPABASE_REF)
    .map(([key]) => key);

  if (sharedPreviewKeys.length > 0) {
    return {
      ok: false,
      message:
        `Vercel PR preview is using the shared preview Supabase project ` +
        `${SHARED_PREVIEW_SUPABASE_REF} via ${sharedPreviewKeys.join(", ")}. ` +
        "PR previews must use Supabase branch-scoped environment variables from " +
        "the matching Supabase preview branch.",
    };
  }

  return { ok: true };
}

if (import.meta.main) {
  const result = validatePreviewSupabaseEnv(process.env);
  if (!result.ok) {
    console.error(result.message);
    process.exit(1);
  }
}
