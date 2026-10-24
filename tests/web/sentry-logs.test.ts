import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dropVerboseSentryLogs } from "../../lib/sentry-logs";

const sentryInit = vi.fn();

vi.mock("@sentry/nextjs", () => ({
  init: sentryInit,
  browserTracingIntegration: vi.fn(() => ({ name: "BrowserTracing" })),
  captureRouterTransitionStart: vi.fn(),
}));

describe("dropVerboseSentryLogs", () => {
  it.each(["trace", "debug"])("drops %s logs", (level) => {
    expect(dropVerboseSentryLogs({ level, message: "noise" })).toBeNull();
  });

  it.each(["info", "warn", "error", "fatal"])(
    "passes %s logs through unchanged",
    (level) => {
      const log = { level, message: "signal", attributes: { a: 1 } };
      expect(dropVerboseSentryLogs(log)).toBe(log);
    },
  );
});

describe("Sentry.init wiring", () => {
  beforeEach(() => {
    vi.resetModules();
    sentryInit.mockClear();
    // Each config warns once when its DSN is unset; keep test output quiet.
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["client", "../../instrumentation-client"],
    ["server", "../../sentry.server.config"],
    ["edge", "../../sentry.edge.config"],
  ])("the %s config filters debug logs before sending", async (_, path) => {
    await import(path);

    expect(sentryInit).toHaveBeenCalledTimes(1);
    const options = sentryInit.mock.calls[0]?.[0] as {
      enableLogs?: boolean;
      beforeSendLog?: typeof dropVerboseSentryLogs;
    };
    expect(options.enableLogs).toBe(true);
    // resetModules re-evaluates lib/sentry-logs, so compare behaviour, not identity.
    const info = { level: "info", message: "kept" };
    expect(
      options.beforeSendLog?.({ level: "debug", message: "x" }),
    ).toBeNull();
    expect(options.beforeSendLog?.(info)).toBe(info);
  });
});
