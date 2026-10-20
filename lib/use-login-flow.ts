"use client";

import { useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { normalizeReturnTo } from "./auth";
import { classifyQueryError, parseHashError } from "./login-errors";
import { getBrowserSupabaseClient } from "./supabase";

type RequestOtpPayload = {
  error?: string;
  message?: string;
  status?: string;
  inviteRequired?: boolean;
};

const INVITE_REQUIRED_FALLBACK =
  "This coach is currently accepting referred athletes only. Enter your invite code to get started.";

async function requestOtp(body: {
  email: string;
  inviteCode: string | null;
  returnTo: string;
}): Promise<{ ok: boolean; payload: RequestOtpPayload }> {
  const response = await fetch("/api/auth/request-otp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  // Gateways and platform errors answer with HTML or plain text; fall back to an
  // empty payload so callers show their own message instead of a parser error.
  const payload = (await response
    .json()
    .catch((): RequestOtpPayload => ({}))) as RequestOtpPayload;
  return { ok: response.ok, payload };
}

async function verifyOtpAndOpenSession(
  email: string,
  token: string,
): Promise<void> {
  const supabase = getBrowserSupabaseClient();
  const { data, error } = await supabase.auth.verifyOtp({
    email,
    token,
    type: "email",
  });

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
}

export type LoginFlow = {
  email: string;
  setEmail: (value: string) => void;
  inviteCode: string;
  setInviteCode: (value: string) => void;
  inviteRequired: boolean;
  otp: string;
  setOtp: (value: string) => void;
  otpSent: boolean;
  status: string | null;
  isError: boolean;
  submitting: boolean;
  handleSendLink: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  handleVerifyOtp: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  changeEmail: () => void;
};

export function useLoginFlow(): LoginFlow {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [inviteRequired, setInviteRequired] = useState(false);
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
    const hashError = parseHashError(window.location.hash.slice(1));
    if (hashError === null) return;
    setStatus(hashError.message);
    setIsError(true);
    if (hashError.resetOtpStep) {
      setOtpSent(false);
    }
    // Remove the hash so it doesn't persist on refresh.
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
  }, []);

  function beginSubmit(): void {
    setSubmitting(true);
    setStatus(null);
    setIsError(false);
  }

  function failSubmit(error: unknown, fallback: string): void {
    setStatus(error instanceof Error ? error.message : fallback);
    setIsError(true);
  }

  async function handleSendLink(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    beginSubmit();

    try {
      const { ok, payload } = await requestOtp({
        email,
        inviteCode: inviteRequired ? inviteCode : null,
        returnTo,
      });

      if (!ok) {
        if (payload.error === "invite_required") {
          setInviteRequired(true);
          setStatus(payload.message ?? INVITE_REQUIRED_FALLBACK);
          setIsError(false);
          return;
        }

        throw new Error(payload.message ?? "Unable to start login.");
      }

      setInviteRequired(false);
      setOtpSent(true);
      setStatus("Check your email for a magic link or 6-digit code.");
    } catch (error) {
      failSubmit(error, "Unable to start login.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerifyOtp(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    beginSubmit();

    try {
      await verifyOtpAndOpenSession(email, otp);
      window.location.href = returnTo;
    } catch (error) {
      failSubmit(error, "Invalid code. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  function changeEmail(): void {
    setOtpSent(false);
    setStatus(null);
    setIsError(false);
  }

  return {
    email,
    setEmail,
    inviteCode,
    setInviteCode,
    inviteRequired,
    otp,
    setOtp,
    otpSent,
    status,
    isError,
    submitting,
    handleSendLink,
    handleVerifyOtp,
    changeEmail,
  };
}
