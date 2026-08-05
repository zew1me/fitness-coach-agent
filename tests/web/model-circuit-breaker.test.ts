import { describe, expect, it } from "vitest";

import {
  createModelCircuitBreaker,
  type ModelAdmission,
} from "../../lib/agent/model-circuit-breaker";
import type { ModelTier } from "../../lib/agent/model-tiers";

const tiers: readonly ModelTier[] = [
  { model: "model-1", effort: "medium", verbosity: "low" },
  { model: "model-2", effort: "medium", verbosity: "low" },
];

function closedAdmission(
  tier: ModelTier = tiers[0] as ModelTier,
): ModelAdmission {
  return { tier };
}

describe("model circuit breaker", () => {
  it("opens after three rate-limited turns and skips the open tier", () => {
    const breaker = createModelCircuitBreaker(() => 1_000);

    breaker.recordFailure(closedAdmission());
    breaker.recordFailure(closedAdmission());
    expect(breaker.snapshot("model-1").state).toBe("closed");
    breaker.recordFailure(closedAdmission());

    expect(breaker.snapshot("model-1").state).toBe("open");
    expect(breaker.pickTier(tiers).tier.model).toBe("model-2");
  });

  it("grants exactly one lazy half-open probe and closes only for its owner", () => {
    let now = 0;
    const breaker = createModelCircuitBreaker(() => now);
    const staleAdmission = breaker.pickTier(tiers);
    for (let turn = 0; turn < 3; turn += 1) {
      breaker.recordFailure(closedAdmission());
    }
    now = 5_000;

    const probe = breaker.pickTier(tiers);
    expect(probe.tier.model).toBe("model-1");
    expect(probe.probeGeneration).toBeDefined();
    expect(breaker.snapshot("model-1")).toMatchObject({
      state: "half_open",
      probeInFlight: true,
    });
    expect(breaker.pickTier(tiers).tier.model).toBe("model-2");

    breaker.recordSuccess(staleAdmission);
    breaker.recordFailure(staleAdmission);
    expect(breaker.snapshot("model-1").state).toBe("half_open");
    breaker.recordSuccess(probe);
    expect(breaker.snapshot("model-1")).toMatchObject({
      consecutiveFailures: 0,
      state: "closed",
      probeInFlight: false,
    });
  });

  it("doubles a failed half-open window and caps exponential growth at sixty seconds", () => {
    let now = 0;
    const breaker = createModelCircuitBreaker(() => now);
    for (let turn = 0; turn < 3; turn += 1) {
      breaker.recordFailure(closedAdmission());
    }

    for (const expectedWindow of [10_000, 20_000, 40_000, 60_000, 60_000]) {
      now = breaker.snapshot("model-1").until;
      const probe = breaker.pickTier(tiers);
      expect(probe.tier.model).toBe("model-1");
      breaker.recordFailure(probe);
      expect(breaker.snapshot("model-1").windowMs).toBe(expectedWindow);
    }
  });

  it("honors provider cooldowns longer than the exponential cap", () => {
    const breaker = createModelCircuitBreaker(() => 0);
    breaker.recordFailure(closedAdmission(), 120_000);
    breaker.recordFailure(closedAdmission(), 120_000);
    breaker.recordFailure(closedAdmission(), 120_000);
    expect(breaker.snapshot("model-1")).toMatchObject({
      state: "open",
      until: 120_000,
      windowMs: 120_000,
    });
  });

  it("releases an abandoned half-open probe back to open", () => {
    let now = 0;
    const breaker = createModelCircuitBreaker(() => now);
    for (let turn = 0; turn < 3; turn += 1) {
      breaker.recordFailure(closedAdmission());
    }
    now = 5_000;
    const probe = breaker.pickTier(tiers);

    breaker.releaseProbe(probe);

    expect(breaker.snapshot("model-1")).toMatchObject({
      state: "open",
      probeInFlight: false,
      until: 10_000,
    });
  });

  it("never shortens an existing open cooldown", () => {
    let now = 0;
    const breaker = createModelCircuitBreaker(() => now);
    for (let turn = 0; turn < 3; turn += 1) {
      breaker.recordFailure(closedAdmission());
    }
    now = 5_000;
    const probe = breaker.pickTier(tiers);
    breaker.recordFailure(probe);
    expect(breaker.snapshot("model-1").until).toBe(15_000);

    now = 6_000;
    breaker.recordFailure(closedAdmission(), 1_000);
    expect(breaker.snapshot("model-1").until).toBeGreaterThanOrEqual(16_000);
  });

  it("falls back to the first tier without stealing an existing probe", () => {
    const breaker = createModelCircuitBreaker(() => 0);
    for (const tier of tiers) {
      for (let turn = 0; turn < 3; turn += 1) {
        breaker.recordFailure(closedAdmission(tier));
      }
    }
    const fallback = breaker.pickTier(tiers);
    expect(fallback.tier).toBe(tiers[0]);
    expect(fallback.probeGeneration).toBeUndefined();
  });
});
