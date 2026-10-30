// The app's own OpenAI model settings, read by `lib/agent/model-tiers.ts`.
// `tests/web/setup.ts` unsets these so tests see the shipped defaults. SDK
// connection settings (OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_ORG_ID,
// OPENAI_AGENTS_*, ...) are deliberately not listed, so the opt-in live test
// keeps them. `setup-env.test.ts` fails if `lib/` reads a name missing here.
export const MODEL_CONFIG_ENV_VARS = [
  "OPENAI_LEAD_MODEL",
  "OPENAI_LEAD_REASONING_EFFORT",
  "OPENAI_TEXT_VERBOSITY",
  "OPENAI_FALLBACK_MODEL_2",
  "OPENAI_FALLBACK_MODEL_3",
  "OPENAI_FALLBACK_REASONING_EFFORT_2",
  "OPENAI_FALLBACK_REASONING_EFFORT_3",
  "OPENAI_SPECIALIST_MODEL",
  "OPENAI_SPECIALIST_REASONING_EFFORT",
  "OPENAI_SPECIALIST_TEXT_VERBOSITY",
  "OPENAI_MAX_RETRIES",
] as const;
