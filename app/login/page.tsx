"use client";

import { useSearchParams } from "next/navigation";
import type { JSX } from "react";
import { FormEvent, Suspense, useState } from "react";

import { normalizeReturnTo } from "../../lib/auth";
import { getBrowserSupabaseClient } from "../../lib/supabase";

function LoginPageContent(): JSX.Element {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [otpSent, setOtpSent] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const returnTo = normalizeReturnTo(searchParams.get("return_to"));
  const authError = searchParams.get("error");

  async function handleSendLink(event: FormEvent<HTMLFormElement>): Promise<void> {
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

      setOtpSent(true);
      setStatus("Check your email for a magic link or 6-digit code.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to start login.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerifyOtp(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setStatus(null);

    try {
      const supabase = getBrowserSupabaseClient();
      const { data, error } = await supabase.auth.verifyOtp({ email, token: otp, type: "magiclink" });

      if (error !== null) {
        throw error;
      }

      const accessToken = data.session?.access_token;
      if (accessToken !== undefined) {
        await fetch("/api/oauth/browser-session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ access_token: accessToken }),
        });
      }

      window.location.href = returnTo;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Invalid code. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="page">
      <section className="page-card">
        <h1>Login</h1>
        <p>Sign in with a magic link so ChatGPT consent can bind to your athlete account.</p>
        {!otpSent ? (
          <form
            onSubmit={(event) => {
              void handleSendLink(event);
            }}
          >
            <label htmlFor="email">Email</label>
            <input
              autoComplete="email"
              className="input"
              id="email"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="athlete@example.com"
              required
              type="email"
              value={email}
            />
            <button className="button" disabled={submitting} type="submit">
              {submitting ? "Sending..." : "Send magic link"}
            </button>
          </form>
        ) : (
          <form
            onSubmit={(event) => {
              void handleVerifyOtp(event);
            }}
          >
            <label htmlFor="otp">6-digit code from email</label>
            <input
              autoComplete="one-time-code"
              className="input"
              id="otp"
              inputMode="numeric"
              maxLength={6}
              onChange={(event) => setOtp(event.target.value)}
              pattern="[0-9]{6}"
              placeholder="123456"
              required
              type="text"
              value={otp}
            />
            <button className="button" disabled={submitting} type="submit">
              {submitting ? "Verifying..." : "Verify code"}
            </button>
            <button
              className="button button-ghost"
              onClick={() => {
                setOtpSent(false);
                setStatus(null);
              }}
              type="button"
            >
              Use a different email
            </button>
          </form>
        )}
        {status !== null || authError !== null ? <p>{status ?? authError}</p> : null}
      </section>
    </main>
  );
}

export default function LoginPage(): JSX.Element {
  return (
    <Suspense
      fallback={
        <main className="page">
          <section className="page-card">
            <p>Loading login…</p>
          </section>
        </main>
      }
    >
      <LoginPageContent />
    </Suspense>
  );
}
