"use client";

import { useSearchParams } from "next/navigation";
import type { JSX } from "react";
import { FormEvent, Suspense, useState } from "react";

import { getBrowserSupabaseClient } from "../../lib/supabase-browser";

function LoginPageContent(): JSX.Element {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const returnTo = searchParams.get("return_to") ?? "/consent";

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setStatus(null);

    try {
      const supabase = getBrowserSupabaseClient();
      const callbackUrl = new URL("/auth/callback", window.location.origin);
      callbackUrl.searchParams.set("return_to", returnTo);

      const { error } = await supabase.auth.signInWithOtp({
        email,
        options: {
          emailRedirectTo: callbackUrl.toString(),
          shouldCreateUser: true
        }
      });

      if (error !== null) {
        throw error;
      }

      setStatus("Magic link sent. Open the email on this device to continue OAuth consent.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to start login.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main>
      <h1>Login</h1>
      <p>Sign in with a Supabase magic link so ChatGPT consent can bind to your athlete account.</p>
      <form
        onSubmit={(event) => {
          void handleSubmit(event);
        }}
      >
        <label htmlFor="email">Email</label>
        <input
          autoComplete="email"
          id="email"
          onChange={(event) => setEmail(event.target.value)}
          placeholder="athlete@example.com"
          required
          type="email"
          value={email}
        />
        <button disabled={submitting} type="submit">
          {submitting ? "Sending..." : "Send magic link"}
        </button>
      </form>
      {status !== null ? <p>{status}</p> : null}
    </main>
  );
}

export default function LoginPage(): JSX.Element {
  return (
    <Suspense fallback={<main><p>Loading login…</p></main>}>
      <LoginPageContent />
    </Suspense>
  );
}
