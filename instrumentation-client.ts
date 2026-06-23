// This file configures the initialization of Sentry on the client.
// The added config here will be used whenever a users loads a page in their browser.
// https://docs.sentry.io/platforms/javascript/guides/nextjs/

import * as Sentry from "@sentry/nextjs";

const dsn = process.env["NEXT_PUBLIC_SENTRY_DSN"];
if (!dsn) {
  // ESLint's no-console allows only warn/error; warn surfaces this once at load.
  console.warn(
    "NEXT_PUBLIC_SENTRY_DSN is not set; client-side Sentry is disabled.",
  );
}

Sentry.init({
  dsn,
  enableLogs: true,
  environment: process.env["NEXT_PUBLIC_VERCEL_ENV"] ?? "development",
  integrations: [Sentry.browserTracingIntegration()],
  tracePropagationTargets: [
    "localhost",
    /^\/api\//,
    /^https:\/\/fitness-coach-agent(-.*)?\.vercel\.app\/api/,
  ],
  tracesSampleRate: 1.0,
});

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
