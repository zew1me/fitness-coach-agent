import { describe, expect, it } from "vitest";

import { MODEL_TIERS } from "../../lib/agent/model-tiers";

// Guards the ambient-env isolation in `tests/web/setup.ts` (issue #544). These
// only fail when the shell exports `OPENAI_*` model settings, which is exactly
// the environment the isolation exists for.
describe("test setup env isolation", () => {
  it("resolves the import-time model ladder from shipped defaults", () => {
    expect(MODEL_TIERS.map((tier) => tier.model)).toEqual([
      "gpt-6-luna",
      "gpt-5.4-mini",
      "gpt-6.1-terra",
    ]);
  });

  it("leaves no OPENAI_* setting but the API key visible to tests", () => {
    const visible = Object.keys(process.env).filter(
      (name) => name.startsWith("OPENAI_") && name !== "OPENAI_API_KEY",
    );
    expect(visible).toEqual([]);
  });
});
