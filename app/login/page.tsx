"use client";

import type { JSX } from "react";
import { Suspense } from "react";

import { BrandMark } from "../../components/brand-mark";
import { siteConfig } from "../../lib/site";
import { type LoginFlow, useLoginFlow } from "../../lib/use-login-flow";

function SendLinkForm({ flow }: { flow: LoginFlow }): JSX.Element {
  return (
    <form
      onSubmit={(event) => {
        void flow.handleSendLink(event);
      }}
    >
      <label htmlFor="email">Email</label>
      <input
        autoComplete="email"
        className="input"
        id="email"
        onChange={(event) => flow.setEmail(event.target.value)}
        placeholder="athlete@example.com"
        required
        type="email"
        value={flow.email}
      />
      {flow.inviteRequired ? (
        <>
          <label htmlFor="invite-code">Invite code</label>
          <input
            autoComplete="one-time-code"
            className="input"
            id="invite-code"
            onChange={(event) => flow.setInviteCode(event.target.value)}
            required
            type="text"
            value={flow.inviteCode}
          />
        </>
      ) : null}
      <button className="button" disabled={flow.submitting} type="submit">
        {flow.submitting ? "Sending..." : "Send code"}
      </button>
    </form>
  );
}

function VerifyOtpForm({ flow }: { flow: LoginFlow }): JSX.Element {
  return (
    <form
      onSubmit={(event) => {
        void flow.handleVerifyOtp(event);
      }}
    >
      <label htmlFor="otp">6-digit code from email</label>
      <input
        autoComplete="one-time-code"
        className="input"
        id="otp"
        inputMode="numeric"
        maxLength={6}
        onChange={(event) => flow.setOtp(event.target.value)}
        pattern="[0-9]{6}"
        placeholder="123456"
        required
        type="text"
        value={flow.otp}
      />
      <button className="button" disabled={flow.submitting} type="submit">
        {flow.submitting ? "Verifying..." : "Verify code"}
      </button>
      <button
        className="button button-ghost"
        onClick={flow.changeEmail}
        type="button"
      >
        Use a different email
      </button>
    </form>
  );
}

function LoginPageContent(): JSX.Element {
  const flow = useLoginFlow();

  return (
    <main className="page">
      <section className="page-card">
        <div className="brand-lockup login-brand">
          <BrandMark />
          <div className="brand-title">
            <h1>{siteConfig.appName}</h1>
            <span>Sign in with a magic link</span>
          </div>
        </div>
        {flow.otpSent ? (
          <VerifyOtpForm flow={flow} />
        ) : (
          <SendLinkForm flow={flow} />
        )}
        {flow.status !== null ? (
          <p className={flow.isError ? "error" : undefined}>{flow.status}</p>
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
