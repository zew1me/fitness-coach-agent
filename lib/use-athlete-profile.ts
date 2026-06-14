"use client";

import { useCallback, useEffect, useState } from "react";

import { loadProfile, saveProfile } from "./coach-api";
import { errorMessage } from "./errors";
import type { AthleteProfile, BrowserTokenResponse } from "./types";

export function emptyProfile(userId: string): AthleteProfile {
  return {
    user_id: userId,
    coaching_state: "onboarding",
    primary_sports: [],
  };
}

export type AthleteProfileHook = {
  profile: AthleteProfile | null;
  setProfile: (_profile: AthleteProfile) => void;
  ensureLoaded: () => Promise<void>;
  save: () => Promise<AthleteProfile | null>;
  saving: boolean;
  status: string | null;
  resetStatus: () => void;
};

export function useAthleteProfile(
  token: BrowserTokenResponse | null,
): AthleteProfileHook {
  const [profile, setProfileState] = useState<AthleteProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    if (token === null) {
      return;
    }
    let cancelled = false;
    async function prefetch(userId: string): Promise<void> {
      try {
        const loaded = await loadProfile(userId);
        if (!cancelled) setProfileState(loaded);
      } catch {
        if (!cancelled) setProfileState(emptyProfile(userId));
      }
    }
    void prefetch(token.user_id);
    return (): void => {
      cancelled = true;
    };
  }, [token]);

  const setProfile = useCallback((next: AthleteProfile) => {
    setProfileState(next);
  }, []);

  const ensureLoaded = useCallback(async (): Promise<void> => {
    if (token === null || profile !== null) return;
    setSaving(true);
    setStatus(null);
    try {
      const loaded = await loadProfile(token.user_id);
      setProfileState(loaded);
    } catch {
      setProfileState(emptyProfile(token.user_id));
    } finally {
      setSaving(false);
    }
  }, [token, profile]);

  const save = useCallback(async (): Promise<AthleteProfile | null> => {
    if (profile === null) return null;
    setSaving(true);
    setStatus(null);
    try {
      const saved = await saveProfile(profile);
      setProfileState(saved);
      setStatus("Saved your athlete settings.");
      return saved;
    } catch (error) {
      setStatus(errorMessage(error, "Unable to save your athlete settings."));
      return null;
    } finally {
      setSaving(false);
    }
  }, [profile]);

  const resetStatus = useCallback(() => setStatus(null), []);

  return {
    profile,
    setProfile,
    ensureLoaded,
    save,
    saving,
    status,
    resetStatus,
  };
}
