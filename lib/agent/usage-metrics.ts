import type { Usage } from "@openai/agents";
import * as Sentry from "@sentry/nextjs";

export function recordStageUsage(
  stage: "compaction" | "delegation" | "lead" | "lead-followup" | "specialist",
  usage?: Usage,
): void {
  if (!usage) return;
  const entries = usage.requestUsageEntries ?? [];
  Sentry.logger.info("coach model stage usage", {
    stage,
    request_count: usage.requests,
    input_tokens: usage.inputTokens,
    cached_tokens: entries.reduce(
      (sum, entry) => sum + (entry.inputTokensDetails["cached_tokens"] ?? 0),
      0,
    ),
    output_tokens: usage.outputTokens,
    reasoning_tokens: entries.reduce(
      (sum, entry) =>
        sum + (entry.outputTokensDetails["reasoning_tokens"] ?? 0),
      0,
    ),
    total_tokens: usage.totalTokens,
    max_request_input: Math.max(
      0,
      ...entries.map((entry) => entry.inputTokens),
    ),
  });
}
