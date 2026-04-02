import { createServerClient, type CookieOptions } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";

const supabaseUrl = process.env["NEXT_PUBLIC_SUPABASE_URL"];
const supabaseAnonKey = process.env["NEXT_PUBLIC_SUPABASE_ANON_KEY"];

export async function createServerSupabaseClient(): Promise<SupabaseClient> {
  if (!supabaseUrl || !supabaseAnonKey) {
    throw new Error("Supabase browser client is not configured.");
  }

  const cookieStore = await cookies();
  return createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(
        cookiesToSet: Array<{
          name: string;
          value: string;
          options?: CookieOptions;
        }>
      ) {
        cookiesToSet.forEach(({ name, value, options }) => {
          cookieStore.set({
            name,
            value,
            ...(options ?? {})
          });
        });
      }
    }
  });
}
