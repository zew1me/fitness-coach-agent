/**
 * Severity levels that are never shipped to Sentry Logs.
 *
 * `Sentry.logger.debug` calls are breadcrumbs for local investigation, but with
 * `enableLogs: true` every one of them was ingested — `"browser token fetched"`
 * alone was the most frequent log in both production and preview (issue #265).
 */
const DROPPED_LEVELS: ReadonlySet<string> = new Set(["trace", "debug"]);

/**
 * `beforeSendLog` hook shared by every `Sentry.init` call: drops trace/debug
 * logs and passes info and above through unchanged.
 */
export function dropVerboseSentryLogs<T extends { level: string }>(
  log: T,
): T | null {
  return DROPPED_LEVELS.has(log.level) ? null : log;
}
