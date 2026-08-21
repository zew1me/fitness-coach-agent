"use client";

import type { JSX } from "react";

import type { AthleteProfile } from "../lib/types";

// Shared chat CSS keeps the drawer styling identical on chat and calendar.
import styles from "./coach-chat.module.css";

function ProfileDrawerFields({
  profile,
  setProfile,
  saving,
  status,
  onSave,
}: Readonly<{
  profile: AthleteProfile;
  setProfile: (_profile: AthleteProfile) => void;
  saving: boolean;
  status: string | null;
  onSave: () => void;
}>): JSX.Element {
  return (
    <div className={styles.fieldGrid}>
      <label className={styles.fieldLabel}>
        Display name
        <input
          className={styles.fieldInput}
          onChange={(event) =>
            setProfile({
              ...profile,
              display_name: event.target.value || null,
            })
          }
          placeholder="Your name (optional)"
          value={profile.display_name ?? ""}
        />
      </label>
      <label className={styles.fieldLabel}>
        Sports (comma-separated)
        <input
          className={styles.fieldInput}
          onChange={(event) =>
            setProfile({
              ...profile,
              primary_sports: event.target.value
                .split(",")
                .map((s) => s.trim())
                .filter((s) => s.length > 0),
            })
          }
          placeholder="e.g. running, cycling, strength"
          value={profile.primary_sports.join(", ")}
        />
      </label>
      <label className={styles.fieldLabel}>
        Weekly training hours
        <input
          className={styles.fieldInput}
          min="0"
          onChange={(event) =>
            setProfile({
              ...profile,
              weekly_available_hours:
                event.target.value === "" ? null : Number(event.target.value),
            })
          }
          step="0.5"
          type="number"
          value={profile.weekly_available_hours ?? ""}
        />
      </label>
      <div className={styles.actionRow}>
        <button
          className={styles.primaryButton}
          disabled={saving}
          onClick={onSave}
          type="button"
        >
          {saving ? "Saving..." : "Save profile"}
        </button>
      </div>
      {status !== null ? <p className={styles.drawerStatus}>{status}</p> : null}
    </div>
  );
}

export function ProfileDrawer({
  open,
  onClose,
  profile,
  setProfile,
  saving,
  status,
  onSave,
}: Readonly<{
  open: boolean;
  onClose: () => void;
  profile: AthleteProfile | null;
  setProfile: (_profile: AthleteProfile) => void;
  saving: boolean;
  status: string | null;
  onSave: () => void;
}>): JSX.Element | null {
  if (!open) return null;
  return (
    <div
      className={styles.drawerBackdrop}
      onClick={onClose}
      role="presentation"
    >
      <aside
        aria-label="Profile and preferences"
        className={styles.drawer}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.drawerHeader}>
          <div>
            <h2 className={styles.drawerTitle}>Profile</h2>
            <p className={styles.drawerText}>
              Review the profile details your coach uses for training guidance.
            </p>
          </div>
          <button
            className={styles.drawerClose}
            onClick={onClose}
            type="button"
          >
            Close
          </button>
        </div>

        <ProfileDrawerBody
          profile={profile}
          saving={saving}
          setProfile={setProfile}
          status={status}
          onSave={onSave}
        />
      </aside>
    </div>
  );
}

function ProfileDrawerBody({
  profile,
  setProfile,
  saving,
  status,
  onSave,
}: Readonly<{
  profile: AthleteProfile | null;
  setProfile: (_profile: AthleteProfile) => void;
  saving: boolean;
  status: string | null;
  onSave: () => void;
}>): JSX.Element {
  if (saving && profile === null) {
    return <p className={styles.drawerStatus}>Loading your settings…</p>;
  }
  if (profile === null) {
    return <p className={styles.drawerStatus}>No profile loaded yet.</p>;
  }
  return (
    <ProfileDrawerFields
      profile={profile}
      saving={saving}
      setProfile={setProfile}
      status={status}
      onSave={onSave}
    />
  );
}
