// This file configures the initialization of Sentry on the server.
// The config you add here will be used whenever the server handles a request.
// https://docs.sentry.io/platforms/javascript/guides/nextjs/

import * as Sentry from "@sentry/nextjs";

const dsn = process.env["SENTRY_DSN"];
if (!dsn) {
  // ESLint's no-console allows only warn/error; warn surfaces this once at load.
  console.warn("SENTRY_DSN is not set; server-side Sentry is disabled.");
}

Sentry.init({
  dsn,
  environment: process.env["APP_ENV"] ?? "development",
  enableLogs: true,
  streamGenAiSpans: true,
  tracesSampleRate: 1.0,
});
