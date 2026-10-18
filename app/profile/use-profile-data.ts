import { useCallback, useState } from "react";

import {
  fetchBrowserToken,
  loadAthleteSummary,
  loadFitnessMetrics,
} from "../../lib/coach-api";
import type { AthleteProfile, FitnessMetrics } from "../../lib/types";

export type ConfirmKey =
  | "cycling_ftp"
  | "max_hr"
  | "run_threshold_pace"
  | "swim_css"
  | "weight";
export type ConfirmFn = (key: ConfirmKey, action: () => Promise<void>) => void;

type ProfileData = {
  confirm: ConfirmFn;
  confirming: ConfirmKey | null;
  error: string | null;
  load: (isCancelled: () => boolean) => Promise<void>;
  metrics: FitnessMetrics | null;
  profile: AthleteProfile | null;
  userId: string;
};

/** Fitness metrics + athlete profile state and the threshold-confirm action. */
export function useProfileData(): ProfileData {
  const [metrics, setMetrics] = useState<FitnessMetrics | null>(null);
  const [profile, setProfile] = useState<AthleteProfile | null>(null);
  const [userId, setUserId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ConfirmKey | null>(null);

  const load = useCallback(
    async (isCancelled: () => boolean): Promise<void> => {
      try {
        const token = await fetchBrowserToken();
        if (isCancelled()) return;
        setUserId(token.user_id);

        const { profile: nextProfile, fitnessMetrics: nextMetrics } =
          await loadAthleteSummary(token.user_id);
        if (!isCancelled()) {
          setMetrics(nextMetrics);
          setProfile(nextProfile);
        }
      } catch (err: unknown) {
        if (!isCancelled()) {
          setError(
            err instanceof Error ? err.message : "Failed to load fitness data.",
          );
        }
      }
    },
    [],
  );

  const confirm = useCallback<ConfirmFn>(
    (key, action): void => {
      if (!userId) return;
      setConfirming(key);
      action()
        .then(() => loadFitnessMetrics(userId))
        .then(setMetrics)
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : "Failed to confirm.");
        })
        .finally((): void => {
          setConfirming(null);
        });
    },
    [userId],
  );

  return { confirm, confirming, error, load, metrics, profile, userId };
}
