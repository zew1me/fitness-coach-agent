import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env["NEXT_PUBLIC_SUPABASE_URL"];
const supabaseAnonKey = process.env["NEXT_PUBLIC_SUPABASE_ANON_KEY"];

export function createBrowserSupabaseClient(
  url: string,
  anonKey: string
): ReturnType<typeof createClient> {
  return createClient(url, anonKey, {
    auth: {
      flowType: "pkce",
      persistSession: true
    }
  });
}

export const supabaseBrowserClient =
  supabaseUrl && supabaseAnonKey
    ? createBrowserSupabaseClient(supabaseUrl, supabaseAnonKey)
    : null;

export function getBrowserSupabaseClient(): NonNullable<typeof supabaseBrowserClient> {
  if (supabaseBrowserClient === null) {
    throw new Error("Supabase browser client is not configured.");
  }

  return supabaseBrowserClient;
}
