import type { ModelTier } from "./model-tiers";

export type BreakerState = "closed" | "open" | "half_open";

export type BreakerSnapshot = {
  consecutiveFailures: number;
  probeInFlight: boolean;
  state: BreakerState;
  until: number;
  windowMs: number;
};

export type ModelAdmission = {
  probeGeneration?: number;
  tier: ModelTier;
};

type BreakerEntry = BreakerSnapshot & { probeGeneration: number };

export type ModelCircuitBreaker = {
  pickTier: (tiers: readonly ModelTier[]) => ModelAdmission;
  recordFailure: (admission: ModelAdmission, retryAfterMs?: number) => void;
  recordSuccess: (admission: ModelAdmission) => void;
  releaseProbe: (admission: ModelAdmission) => void;
  reset: () => void;
  snapshot: (model: string) => BreakerSnapshot;
};

const FAILURE_THRESHOLD = 3;
const MIN_COOLDOWN_MS = 5_000;
const MAX_COOLDOWN_MS = 60_000;

/**
 * Creates an in-memory model circuit breaker. State is per serverless instance,
 * not org-global. Vercel may freeze an instance after a response, so there is no
 * timer heartbeat: the first request after the cooldown lazily becomes the
 * single half-open probe.
 */
export function createModelCircuitBreaker(
  now: () => number = Date.now,
): ModelCircuitBreaker {
  const entries = new Map<string, BreakerEntry>();

  const entryFor = (model: string): BreakerEntry => {
    const existing = entries.get(model);
    if (existing) return existing;
    const created: BreakerEntry = {
      consecutiveFailures: 0,
      probeInFlight: false,
      probeGeneration: 0,
      state: "closed",
      until: 0,
      windowMs: MIN_COOLDOWN_MS,
    };
    entries.set(model, created);
    return created;
  };

  const admit = (tier: ModelTier): ModelAdmission | undefined => {
    const entry = entryFor(tier.model);
    if (entry.state === "closed") return { tier };
    if (entry.state === "half_open" || now() < entry.until) return undefined;
    entry.state = "half_open";
    entry.probeInFlight = true;
    entry.probeGeneration += 1;
    return { tier, probeGeneration: entry.probeGeneration };
  };

  const ownsProbe = (entry: BreakerEntry, admission: ModelAdmission): boolean =>
    entry.state === "half_open" &&
    admission.probeGeneration !== undefined &&
    admission.probeGeneration === entry.probeGeneration;

  return {
    pickTier(tiers: readonly ModelTier[]): ModelAdmission {
      if (tiers.length === 0)
        throw new Error("At least one model tier is required");
      for (const tier of tiers) {
        const admission = admit(tier);
        if (admission) return admission;
      }
      // Service is preferable to an eager refusal. When every circuit is open,
      // try the first tier without granting ownership of an existing probe.
      return { tier: tiers[0] as ModelTier };
    },

    recordFailure(admission: ModelAdmission, retryAfterMs?: number): void {
      const entry = entryFor(admission.tier.model);
      if (entry.state === "half_open" && !ownsProbe(entry, admission)) {
        return;
      }
      entry.consecutiveFailures += 1;
      const requestedWindow = Math.max(MIN_COOLDOWN_MS, retryAfterMs ?? 0);

      // A result admitted before another request opened the circuit is stale.
      // It may extend an open cooldown, but must never shorten it.
      if (entry.state === "open") {
        entry.windowMs = Math.max(entry.windowMs, requestedWindow);
        entry.until = Math.max(entry.until, now() + entry.windowMs);
        return;
      }
      if (
        entry.state === "closed" &&
        entry.consecutiveFailures < FAILURE_THRESHOLD
      ) {
        return;
      }

      entry.windowMs =
        entry.state === "half_open"
          ? Math.max(
              entry.windowMs,
              Math.min(entry.windowMs * 2, MAX_COOLDOWN_MS),
              requestedWindow,
            )
          : requestedWindow;
      entry.state = "open";
      entry.probeInFlight = false;
      entry.until = now() + entry.windowMs;
    },

    recordSuccess(admission: ModelAdmission): void {
      const entry = entryFor(admission.tier.model);
      // A closed request that completes after concurrent failures opened the
      // circuit must not close it. Only the owning half-open probe can do so.
      if (entry.state === "open") return;
      if (entry.state === "half_open" && !ownsProbe(entry, admission)) return;
      entry.consecutiveFailures = 0;
      entry.probeInFlight = false;
      entry.state = "closed";
      entry.until = 0;
      entry.windowMs = MIN_COOLDOWN_MS;
    },

    releaseProbe(admission: ModelAdmission): void {
      const entry = entryFor(admission.tier.model);
      if (!ownsProbe(entry, admission)) return;
      entry.state = "open";
      entry.probeInFlight = false;
      entry.until = now() + entry.windowMs;
    },

    // Telemetry-only read: never allocates an entry, so tagging a Sentry event
    // for a model the breaker has never admitted (a specialist tier, say) does
    // not grow the map.
    snapshot(model: string): BreakerSnapshot {
      const entry = entries.get(model);
      if (!entry) {
        return {
          consecutiveFailures: 0,
          probeInFlight: false,
          state: "closed",
          until: 0,
          windowMs: MIN_COOLDOWN_MS,
        };
      }
      return {
        consecutiveFailures: entry.consecutiveFailures,
        probeInFlight: entry.probeInFlight,
        state: entry.state,
        until: entry.until,
        windowMs: entry.windowMs,
      };
    },

    reset(): void {
      entries.clear();
    },
  };
}

export const modelCircuitBreaker = createModelCircuitBreaker();
