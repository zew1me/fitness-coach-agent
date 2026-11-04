import { beforeEach, describe, expect, it, vi } from "vitest";

const sentryMocks = vi.hoisted(() => ({
  logger: { debug: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn() },
}));

vi.mock("@sentry/nextjs", () => sentryMocks);

import {
  consoleSink,
  createLogger,
  noopLogger,
  sentrySink,
  type Logger,
} from "../../lib/logger";

function spyLogger(): Logger & Record<keyof Logger, ReturnType<typeof vi.fn>> {
  return { debug: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn() };
}

beforeEach(() => {
  vi.resetAllMocks();
});

describe("createLogger", () => {
  it("forwards every level to both Sentry and console by default", () => {
    const consoleSpies = {
      debug: vi.spyOn(console, "debug").mockImplementation(() => {}),
      error: vi.spyOn(console, "error").mockImplementation(() => {}),
      info: vi.spyOn(console, "info").mockImplementation(() => {}),
      warn: vi.spyOn(console, "warn").mockImplementation(() => {}),
    };
    const logger = createLogger();

    for (const level of ["debug", "info", "warn", "error"] as const) {
      logger[level](`chat: ${level}`, { user_id: "athlete-1" });
      expect(sentryMocks.logger[level]).toHaveBeenCalledWith(`chat: ${level}`, {
        user_id: "athlete-1",
      });
      expect(consoleSpies[level]).toHaveBeenCalledWith(`chat: ${level}`, {
        user_id: "athlete-1",
      });
    }

    for (const spy of Object.values(consoleSpies)) spy.mockRestore();
  });

  it("omits the attributes argument when none were given", () => {
    const target = spyLogger();
    const sentry = spyLogger();
    const logger = createLogger([
      consoleSink(target),
      sentrySink(() => sentry),
    ]);

    logger.warn("chat: missing browser session cookie");

    expect(target.warn).toHaveBeenCalledWith(
      "chat: missing browser session cookie",
    );
    expect(sentry.warn).toHaveBeenCalledWith(
      "chat: missing browser session cookie",
    );
  });

  it("still reaches the other sinks when one sink throws", () => {
    const failing = spyLogger();
    failing.error.mockImplementation(() => {
      throw new Error("transport down");
    });
    const healthy = spyLogger();
    const logger = createLogger([failing, healthy]);

    expect(() =>
      logger.error("chat: POST error", { error: "boom" }),
    ).not.toThrow();
    expect(healthy.error).toHaveBeenCalledWith("chat: POST error", {
      error: "boom",
    });
  });

  it("writes only to the sinks it was given", () => {
    const sink = spyLogger();
    createLogger([sink]).info("coach turn complete", { textStarted: true });

    expect(sink.info).toHaveBeenCalledWith("coach turn complete", {
      textStarted: true,
    });
    expect(sentryMocks.logger.info).not.toHaveBeenCalled();
  });

  it("noopLogger writes nowhere", () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    noopLogger.info("coach turn complete");

    expect(info).not.toHaveBeenCalled();
    expect(sentryMocks.logger.info).not.toHaveBeenCalled();
    info.mockRestore();
  });
});
