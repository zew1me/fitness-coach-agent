import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env["NEXT_PUBLIC_SUPABASE_URL"];
const supabaseAnonKey = process.env["NEXT_PUBLIC_SUPABASE_ANON_KEY"];

export const supabaseBrowserClient =
  supabaseUrl && supabaseAnonKey
    ? createClient(supabaseUrl, supabaseAnonKey, {
        auth: {
          persistSession: true
        }
      })
    : null;

export function getBrowserSupabaseClient(): NonNullable<typeof supabaseBrowserClient> {
  if (supabaseBrowserClient === null) {
    throw new Error("Supabase browser client is not configured.");
  }

  return supabaseBrowserClient;
}
