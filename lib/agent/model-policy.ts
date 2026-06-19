export type BoundedReasoningEffort = "none" | "low" | "medium";
export type TextVerbosity = "low" | "medium";

export type AgentModelPolicy = {
  leadModel: string;
  leadReasoningEffort: BoundedReasoningEffort;
  leadTextVerbosity: TextVerbosity;
  specialistModel: string;
  specialistReasoningEffort: BoundedReasoningEffort;
  specialistTextVerbosity: TextVerbosity;
};

type AgentEnvironment = Readonly<Record<string, string | undefined>>;

const REASONING_EFFORTS = new Set<BoundedReasoningEffort>([
  "none",
  "low",
  "medium",
]);

function boundedReasoningEffort(
  value: string | undefined,
  fallback: BoundedReasoningEffort,
): BoundedReasoningEffort {
  return REASONING_EFFORTS.has(value as BoundedReasoningEffort)
    ? (value as BoundedReasoningEffort)
    : fallback;
}

const TEXT_VERBOSITIES = new Set<TextVerbosity>(["low", "medium"]);

function textVerbosity(value: string | undefined): TextVerbosity {
  return TEXT_VERBOSITIES.has(value as TextVerbosity)
    ? (value as TextVerbosity)
    : "low";
}

export function loadAgentModelPolicy(
  env: AgentEnvironment = process.env,
): AgentModelPolicy {
  return {
    leadModel: env["OPENAI_LEAD_MODEL"] || "gpt-5.5",
    leadReasoningEffort: boundedReasoningEffort(
      env["OPENAI_LEAD_REASONING_EFFORT"],
      "medium",
    ),
    specialistModel: env["OPENAI_SPECIALIST_MODEL"] || "gpt-5.4-mini",
    specialistReasoningEffort: boundedReasoningEffort(
      env["OPENAI_SPECIALIST_REASONING_EFFORT"],
      "low",
    ),
    specialistTextVerbosity: textVerbosity(
      env["OPENAI_SPECIALIST_TEXT_VERBOSITY"],
    ),
    leadTextVerbosity: textVerbosity(env["OPENAI_TEXT_VERBOSITY"]),
  };
}
