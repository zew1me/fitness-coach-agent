import * as Sentry from "@sentry/nextjs";
import { useState } from "react";
import type { RefObject } from "react";

import type { AthleteProfileHook } from "./use-athlete-profile";

type ProfileDrawerOptions = {
  athleteProfile: AthleteProfileHook;
  olderRequestVersionRef: RefObject<number>;
  refetchThread: () => Promise<void>;
  resetWindow: () => void;
};

type ProfileDrawer = {
  drawerOpen: boolean;
  openDrawer: () => Promise<void>;
  closeDrawer: () => void;
  saveProfile: () => Promise<void>;
};

/** Opens the profile drawer and refreshes the thread after a profile save. */
export function useProfileDrawer({
  athleteProfile,
  olderRequestVersionRef,
  refetchThread,
  resetWindow,
}: ProfileDrawerOptions): ProfileDrawer {
  const [drawerOpen, setDrawerOpen] = useState(false);

  async function openDrawer(): Promise<void> {
    setDrawerOpen(true);
    await athleteProfile.ensureLoaded();
  }

  function closeDrawer(): void {
    setDrawerOpen(false);
  }

  async function saveProfile(): Promise<void> {
    const saved = await athleteProfile.save();
    if (saved === null) return;
    try {
      olderRequestVersionRef.current += 1;
      await refetchThread();
      resetWindow();
    } catch (refreshError) {
      Sentry.logger.warn("chat thread refresh failed after profile save");
      console.warn(
        "Profile saved but chat thread refresh failed",
        refreshError,
      );
    }
  }

  return { closeDrawer, drawerOpen, openDrawer, saveProfile };
}
