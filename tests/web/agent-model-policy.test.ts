import { describe, expect, it } from "vitest";

import { loadAgentModelPolicy } from "../../lib/agent/model-policy";

describe("loadAgentModelPolicy", () => {
  it("defaults the lead coach to GPT-5.5 with medium reasoning", () => {
    expect(loadAgentModelPolicy({})).toMatchObject({
      leadModel: "gpt-5.5",
      leadReasoningEffort: "medium",
      specialistModel: "gpt-5.4-mini",
      specialistReasoningEffort: "low",
      specialistTextVerbosity: "low",
      leadTextVerbosity: "low",
    });
  });

  it.each(["high", "xhigh", "unsupported"])(
    "caps unsupported lead reasoning effort %s at medium",
    (configuredEffort) => {
      expect(
        loadAgentModelPolicy({
          OPENAI_LEAD_REASONING_EFFORT: configuredEffort,
        }).leadReasoningEffort,
      ).toBe("medium");
    },
  );

  it.each(["none", "low", "medium"] as const)(
    "accepts bounded lead reasoning effort %s",
    (configuredEffort) => {
      expect(
        loadAgentModelPolicy({
          OPENAI_LEAD_REASONING_EFFORT: configuredEffort,
        }).leadReasoningEffort,
      ).toBe(configuredEffort);
    },
  );

  it.each(["high", "xhigh", "unsupported"])(
    "caps unsupported specialist reasoning effort %s at low",
    (configuredEffort) => {
      expect(
        loadAgentModelPolicy({
          OPENAI_SPECIALIST_REASONING_EFFORT: configuredEffort,
        }).specialistReasoningEffort,
      ).toBe("low");
    },
  );

  it.each(["none", "low", "medium"] as const)(
    "accepts bounded specialist reasoning effort %s",
    (configuredEffort) => {
      expect(
        loadAgentModelPolicy({
          OPENAI_SPECIALIST_REASONING_EFFORT: configuredEffort,
        }).specialistReasoningEffort,
      ).toBe(configuredEffort);
    },
  );

  it.each(["high", "unsupported", ""])(
    "caps unsupported specialist text verbosity %s at low",
    (configuredVerbosity) => {
      expect(
        loadAgentModelPolicy({
          OPENAI_SPECIALIST_TEXT_VERBOSITY: configuredVerbosity,
        }).specialistTextVerbosity,
      ).toBe("low");
    },
  );

  it("accepts medium specialist text verbosity", () => {
    expect(
      loadAgentModelPolicy({
        OPENAI_SPECIALIST_TEXT_VERBOSITY: "medium",
      }).specialistTextVerbosity,
    ).toBe("medium");
  });
});
