"use client";

import { useSearchParams } from "next/navigation";
import type { JSX } from "react";
import { FormEvent, Suspense, useEffect, useState } from "react";

import { normalizeReturnTo } from "../../lib/auth";
import { getBrowserSupabaseClient } from "../../lib/supabase";

function classifySupabaseErrorCode(code: string): string {
  switch (code) {
    case "otp_expired":
      return "Your sign-in link has expired. Enter your email to get a new one.";
    case "bad_otp":
      return "That sign-in link is not valid. Request a new one.";
    case "email_not_confirmed":
      return "Please confirm your email address first, then try signing in.";
    case "access_denied":
      return "Access was denied. Try signing in again.";
    default:
      return "There was a problem signing you in. Try requesting a new link.";
  }
}

function classifyQueryError(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.includes("missing auth code") || lower.includes("already been used")) {
    return "Your sign-in link is missing or has already been used. Enter your email to get a new one.";
  }
  if (lower.includes("unable to finish login") || lower.includes("unable to finish")) {
    return "Something went wrong completing your sign-in. Try again.";
  }
  return "There was a problem signing you in. Try requesting a new link.";
}

/** Returns true for error codes that indicate an expired / invalid link. */
function isLinkError(code: string): boolean {
  return code === "otp_expired" || code === "bad_otp" || code === "access_denied";
}

function LoginPageContent(): JSX.Element {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [otpSent, setOtpSent] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [isError, setIsError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const returnTo = normalizeReturnTo(searchParams.get("return_to"));

  // Parse query-string error set by the auth callback route.
  useEffect(() => {
    const queryError = searchParams.get("error");
    if (queryError) {
      setStatus(classifyQueryError(queryError));
      setIsError(true);
      setOtpSent(false);
    }
  }, [searchParams]);

  // Parse hash-fragment errors set by Supabase (not visible to server).
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (!hash) return;
    const params = new URLSearchParams(hash);
    const errorCode = params.get("error_code");
    if (errorCode) {
      setStatus(classifySupabaseErrorCode(errorCode));
      setIsError(true);
      if (isLinkError(errorCode)) {
        setOtpSent(false);
      }
      // Remove the hash so it doesn't persist on refresh.
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, []);

  async function handleSendLink(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setStatus(null);
    setIsError(false);

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
      setIsError(true);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerifyOtp(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setStatus(null);
    setIsError(false);

    try {
      const supabase = getBrowserSupabaseClient();
      const { data, error } = await supabase.auth.verifyOtp({ email, token: otp, type: "email" });

      if (error !== null) {
        throw error;
      }

      const accessToken = data.session?.access_token;
      if (accessToken === undefined) {
        throw new Error("No session returned after OTP verification.");
      }

      const sessionRes = await fetch("/api/oauth/browser-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: accessToken }),
      });
      if (!sessionRes.ok) {
        throw new Error("Failed to establish browser session.");
      }

      window.location.href = returnTo;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Invalid code. Try again.");
      setIsError(true);
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
                setIsError(false);
              }}
              type="button"
            >
              Use a different email
            </button>
          </form>
        )}
        {status !== null ? (
          <p className={isError ? "error" : undefined}>{status}</p>
        ) : null}
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
