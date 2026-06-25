// This file configures the initialization of Sentry for edge features (middleware, edge routes, and so on).
// The config you add here will be used whenever one of the edge features is loaded.
// Note that this config is unrelated to the Vercel Edge Runtime and is also required when running locally.
// https://docs.sentry.io/platforms/javascript/guides/nextjs/

import * as Sentry from "@sentry/nextjs";

const dsn = process.env["SENTRY_DSN"];
if (!dsn) {
  // ESLint's no-console allows only warn/error; warn surfaces this once at load.
  console.warn("SENTRY_DSN is not set; edge Sentry is disabled.");
}

Sentry.init({
  dsn,
  environment: process.env["APP_ENV"] ?? "development",
  enableLogs: true,
  tracesSampleRate: 1.0,
});
