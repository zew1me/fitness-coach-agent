import * as Sentry from "@sentry/nextjs";

/**
 * Structured key/value context, the same shape `Sentry.logger.*` accepts.
 */
export type LogAttributes = Record<string, unknown>;

export type LogLevel = "debug" | "info" | "warn" | "error";

type LogMethod = (message: string, attributes?: LogAttributes) => void;

/**
 * The server-side logging surface. Code that logs receives one of these (by
 * parameter or option) instead of calling `Sentry.logger` or `console`
 * directly, so tests can pass a spy or {@link noopLogger} (issue #267).
 */
export type Logger = Readonly<Record<LogLevel, LogMethod>>;

/**
 * Sentry Logs: structured, queryable next to traces. Logs emitted inside an
 * active span are already associated with that trace by the SDK.
 *
 * The target is read when a log is written, not when the sink is built, so a
 * module mock of `@sentry/nextjs` installed by a test is still honoured.
 */
export function sentrySink(
  target: () => Pick<typeof Sentry.logger, LogLevel> = () => Sentry.logger,
): Logger {
  return sinkFrom((level, message, attributes) => {
    if (attributes === undefined) {
      target()[level](message);
    } else {
      target()[level](message, attributes);
    }
  });
}

/**
 * stdout/stderr, which Vercel keeps as runtime logs. This is the channel that
 * still works when Sentry's transport is degraded or a flush times out.
 */
export function consoleSink(target: Pick<Console, LogLevel> = console): Logger {
  return sinkFrom((level, message, attributes) => {
    if (attributes === undefined) {
      target[level](message);
    } else {
      target[level](message, attributes);
    }
  });
}

function sinkFrom(
  write: (level: LogLevel, message: string, attributes?: LogAttributes) => void,
): Logger {
  return {
    debug: (message, attributes) => write("debug", message, attributes),
    info: (message, attributes) => write("info", message, attributes),
    warn: (message, attributes) => write("warn", message, attributes),
    error: (message, attributes) => write("error", message, attributes),
  };
}

/**
 * Fans every log out to each sink. A sink that throws never stops the others
 * from receiving the log, and never turns logging into a failure of the caller.
 */
export function createLogger(
  sinks: readonly Logger[] = [sentrySink(), consoleSink()],
): Logger {
  const fanOut =
    (level: LogLevel): LogMethod =>
    (message, attributes) => {
      for (const sink of sinks) {
        try {
          sink[level](message, attributes);
        } catch {
          // Losing one channel is exactly the case the other sink exists for;
          // rethrowing would fail a request because a log line was lost.
        }
      }
    };
  return {
    debug: fanOut("debug"),
    info: fanOut("info"),
    warn: fanOut("warn"),
    error: fanOut("error"),
  };
}

export const noopLogger: Logger = createLogger([]);
