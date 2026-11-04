import type { Usage } from "@openai/agents";

import { defaultLogger, type Logger } from "../logger";

export function recordStageUsage(
  stage: "compaction" | "delegation" | "lead" | "lead-followup" | "specialist",
  usage?: Usage,
  logger: Logger = defaultLogger,
): void {
  if (!usage) return;
  const entries = usage.requestUsageEntries ?? [];
  const maxRequestInput = Math.max(
    0,
    ...entries.map((entry) => entry.inputTokens),
  );
  const requestSizeTokens =
    entries.length > 0 ? maxRequestInput : usage.inputTokens;
  logger.info("coach model stage usage", {
    stage,
    request_count: usage.requests,
    input_tokens: usage.inputTokens,
    request_size_tokens: requestSizeTokens,
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
    max_request_input: maxRequestInput,
  });
}
