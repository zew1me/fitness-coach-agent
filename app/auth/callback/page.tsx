"use client";

import { useSearchParams } from "next/navigation";
import type { JSX } from "react";
import { Suspense, useEffect, useState } from "react";

import { normalizeReturnTo } from "../../../lib/auth";
import { getBrowserSupabaseClient } from "../../../lib/supabase";

function AuthCallbackPageContent(): JSX.Element {
  const searchParams = useSearchParams();
  const [message, setMessage] = useState("Completing login...");

  useEffect(() => {
    const code = searchParams.get("code");
    const returnTo = normalizeReturnTo(searchParams.get("return_to"));

    async function completeLogin(): Promise<void> {
      if (code === null) {
        setMessage("Missing auth code from Supabase.");
        return;
      }

      try {
        const supabase = getBrowserSupabaseClient();
        const { data, error } = await supabase.auth.exchangeCodeForSession(code);

        if (error !== null) {
          throw error;
        }

        const response = await fetch("/api/oauth/browser-session", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ access_token: data.session.access_token })
        });

        if (!response.ok) {
          throw new Error("Unable to establish the OAuth browser session.");
        }

        window.location.assign(returnTo);
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Unable to finish login.");
      }
    }

    void completeLogin();
  }, [searchParams]);

  return (
    <main>
      <h1>Finishing sign-in</h1>
      <p>{message}</p>
    </main>
  );
}

export default function AuthCallbackPage(): JSX.Element {
  return (
    <Suspense fallback={<main><p>Completing login...</p></main>}>
      <AuthCallbackPageContent />
    </Suspense>
  );
}
