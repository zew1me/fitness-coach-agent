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
    cached_tokens: entries.reduce((sum, entry) => {
      const cached = entry.inputTokensDetails["cached_tokens"];
      return sum + (typeof cached === "number" ? cached : 0);
    }, 0),
    output_tokens: usage.outputTokens,
    reasoning_tokens: entries.reduce((sum, entry) => {
      const reasoning = entry.outputTokensDetails["reasoning_tokens"];
      return sum + (typeof reasoning === "number" ? reasoning : 0);
    }, 0),
    total_tokens: usage.totalTokens,
    max_request_input: maxRequestInput,
  });
}
