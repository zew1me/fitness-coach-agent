"use client";

import { useRef, useState } from "react";
import type { JSX, ReactNode } from "react";

import type { AthleteProfile } from "../lib/types";

// Shared chat CSS keeps the account menu styling identical on chat and calendar.
import styles from "./coach-chat.module.css";
import { ThemeSwitcher } from "./theme-switcher";

function accountLabel(profile: AthleteProfile | null): string {
  if (profile === null) return "Account";
  const displayName = profile.display_name?.trim();
  return displayName ? displayName : "Account";
}

function AccountMenu({
  profile,
  onOpenProfile,
  extraItems,
  onMenuItemClick,
}: Readonly<{
  profile: AthleteProfile | null;
  onOpenProfile: () => void;
  extraItems: ReactNode | undefined;
  onMenuItemClick: () => void;
}>): JSX.Element {
  return (
    <div
      aria-label="Account"
      className={styles.accountMenu}
      onClick={(event) => {
        const target = event.target;
        if (
          target instanceof HTMLElement &&
          target.closest('[role="menuitem"]') !== null
        ) {
          onMenuItemClick();
        }
      }}
      role="menu"
    >
      <div className={styles.accountSummary}>
        <span>Signed in</span>
        <strong>{accountLabel(profile)}</strong>
      </div>
      <div className={styles.menuThemeRow}>
        <ThemeSwitcher />
      </div>
      <button
        className={styles.menuItem}
        onClick={onOpenProfile}
        role="menuitem"
        type="button"
      >
        Profile
      </button>
      {extraItems}
      <form
        action="/api/oauth/browser-session/logout"
        className={styles.menuForm}
        method="post"
      >
        <button className={styles.menuItem} role="menuitem" type="submit">
          Sign out
        </button>
      </form>
    </div>
  );
}

export function AccountMenuButton({
  profile,
  onOpenProfile,
  extraItems,
}: Readonly<{
  profile: AthleteProfile | null;
  onOpenProfile: () => void;
  extraItems?: ReactNode;
}>): JSX.Element {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  return (
    <div className={styles.accountMenuWrap}>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Account menu"
        className={styles.accountButton}
        onClick={() => setOpen((prev) => !prev)}
        ref={triggerRef}
        title="Account"
        type="button"
      >
        <svg
          aria-hidden="true"
          className={styles.accountIcon}
          viewBox="0 0 24 24"
        >
          <circle
            cx="12"
            cy="8"
            fill="none"
            r="3.5"
            stroke="currentColor"
            strokeWidth="1.8"
          />
          <path
            d="M5 19.5a7 7 0 0 1 14 0"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeWidth="1.8"
          />
        </svg>
      </button>
      {open ? (
        <AccountMenu
          extraItems={extraItems}
          onMenuItemClick={() => setOpen(false)}
          onOpenProfile={() => {
            setOpen(false);
            triggerRef.current?.focus();
            onOpenProfile();
          }}
          profile={profile}
        />
      ) : null}
    </div>
  );
}
