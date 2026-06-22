// This file configures the initialization of Sentry on the client.
// The added config here will be used whenever a users loads a page in their browser.
// https://docs.sentry.io/platforms/javascript/guides/nextjs/

import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env["NEXT_PUBLIC_SENTRY_DSN"],
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
