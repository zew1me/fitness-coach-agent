"use client";

import Link from "next/link";
import { useEffect } from "react";
import type { JSX } from "react";

import { StatusCard } from "../../components/status-card";

import { IntervalsSection } from "./intervals-section";
import { LoadedView } from "./metric-sections";
import styles from "./profile.module.css";
import { useIntervalsConnection } from "./use-intervals-connection";
import { useProfileData } from "./use-profile-data";

export default function ProfilePage(): JSX.Element {
  const data = useProfileData();
  const intervals = useIntervalsConnection();
  const { load: loadProfile, metrics, error, userId } = data;
  const { consumeReturnParam, loadStatus } = intervals;

  useEffect(() => {
    let cancelled = false;
    const isCancelled = (): boolean => cancelled;
    consumeReturnParam();
    void (async (): Promise<void> => {
      await loadProfile(isCancelled);
      await loadStatus(isCancelled);
    })();
    return (): void => {
      cancelled = true;
    };
  }, [consumeReturnParam, loadProfile, loadStatus]);

  return (
    <div className={styles.page}>
      <div className={styles.shell}>
        <nav className={styles.nav}>
          <Link className={styles.backLink} href="/">
            ← Back to coach
          </Link>
        </nav>
        <h1 className={styles.pageTitle}>Fitness profile</h1>
        {error !== null && (
          <StatusCard
            body={error}
            role="alert"
            title="Unable to load profile"
          />
        )}
        {metrics === null && error === null && (
          <StatusCard
            body="Your latest fitness metrics and connected services will appear here."
            role="status"
            title="Loading profile…"
          />
        )}
        {(metrics !== null || error !== null) && (
          <IntervalsSection
            action={intervals.action}
            error={intervals.error}
            notice={intervals.notice}
            onConnect={intervals.connect}
            onDisconnect={intervals.disconnect}
            onSync={intervals.sync}
            status={intervals.status}
          />
        )}
        {metrics !== null && userId !== "" && (
          <LoadedView
            confirming={data.confirming}
            metrics={metrics}
            onConfirm={data.confirm}
            profile={data.profile}
            userId={userId}
          />
        )}
      </div>
    </div>
  );
}
