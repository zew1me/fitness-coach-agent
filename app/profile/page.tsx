"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { JSX } from "react";

import { StatusCard } from "../../components/status-card";
import {
  confirmProfileMetric,
  confirmSportThreshold,
  disconnectIntervals,
  fetchBrowserToken,
  loadAthleteSummary,
  loadFitnessMetrics,
  loadIntervalsStatus,
  startIntervalsAuthorization,
  syncIntervals,
} from "../../lib/coach-api";
import type {
  AthleteProfile,
  BestTime,
  FitnessMetrics,
  IntervalsConnectionStatus,
  ThresholdSource,
  ThresholdValue,
} from "../../lib/types";

import styles from "./profile.module.css";

// ── Helpers ──────────────────────────────────────────────────

function formatMeasuredAt(measured_at: string | null): string {
  if (!measured_at) return "";
  const d = new Date(measured_at);
  const diffDays = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (diffDays < 1) return "today";
  if (diffDays === 1) return "yesterday";
  if (diffDays < 30) return `${diffDays}d ago`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)}mo ago`;
  return `${Math.floor(diffDays / 365)}y ago`;
}

function isStale(measured_at: string | null): boolean {
  if (!measured_at) return false;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 90);
  return new Date(measured_at) < cutoff;
}

function formatPace(sec: number): string {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")} /km`;
}

function formatCss(sec: number): string {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")} /100m`;
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0)
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function displayValue(tv: ThresholdValue): string {
  if (tv.unit === "sec/km") return formatPace(tv.value);
  if (tv.unit === "sec/100m") return formatCss(tv.value);
  if (tv.unit === "W") return `${tv.value} W`;
  if (tv.unit === "bpm") return `${tv.value} bpm`;
  if (tv.unit === "kg") return `${tv.value} kg`;
  return `${tv.value} ${tv.unit}`;
}

// ── Source badge ─────────────────────────────────────────────

const SOURCE_LABELS: Record<ThresholdSource, string> = {
  estimated: "estimated",
  file: "from file",
  user: "confirmed",
};

function SourceBadge({ source }: { source: ThresholdSource }): JSX.Element {
  return (
    <span className={`${styles.badge} ${styles[`badge_${source}`]}`}>
      {SOURCE_LABELS[source]}
    </span>
  );
}

// ── Single metric row ─────────────────────────────────────────

type MetricRowProps = {
  confirming: boolean;
  label: string;
  onConfirm: (() => void) | null;
  tv: ThresholdValue;
};

function AgeLabel({
  measured_at,
}: {
  measured_at: string | null;
}): JSX.Element | null {
  if (!measured_at) return null;
  const stale = isStale(measured_at);
  const text = formatMeasuredAt(measured_at) + (stale ? " · stale" : "");
  return (
    <span className={`${styles.age} ${stale ? styles.stale : ""}`}>{text}</span>
  );
}

function MetricRow({
  label,
  tv,
  onConfirm,
  confirming,
}: MetricRowProps): JSX.Element {
  return (
    <div className={styles.metricRow}>
      <div className={styles.metricLabel}>{label}</div>
      <div className={styles.metricValue}>
        {tv.source === "estimated" ? (
          <span className={styles.estimated}>~{displayValue(tv)}</span>
        ) : (
          <span>{displayValue(tv)}</span>
        )}
        <SourceBadge source={tv.source} />
        <AgeLabel measured_at={tv.measured_at} />
        {tv.notes !== null && <span className={styles.notes}>{tv.notes}</span>}
      </div>
      {tv.source === "estimated" && onConfirm !== null && (
        <button
          className={styles.confirmBtn}
          disabled={confirming}
          onClick={onConfirm}
          type="button"
        >
          {confirming ? "Confirming…" : "Confirm"}
        </button>
      )}
    </div>
  );
}

// ── Best times ────────────────────────────────────────────────

function BestTimeRow({ bt }: { bt: BestTime }): JSX.Element {
  return (
    <div className={styles.metricRow}>
      <div className={styles.metricLabel}>{bt.distance_label}</div>
      <div className={styles.metricValue}>
        <span>{formatTime(bt.time_seconds)}</span>
        <AgeLabel measured_at={bt.measured_at} />
      </div>
    </div>
  );
}

function BestTimesSection({
  times,
}: {
  times: BestTime[];
}): JSX.Element | null {
  if (times.length === 0) return null;
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Personal bests</h2>
      {times.map((bt) => (
        <BestTimeRow bt={bt} key={bt.distance_label} />
      ))}
    </section>
  );
}

// ── Nutrition ─────────────────────────────────────────────────

function NutritionSection({
  profile,
}: {
  profile: AthleteProfile | null;
}): JSX.Element | null {
  if (profile === null) return null;
  const restrictions = profile.dietary_restrictions ?? [];
  const notes = profile.nutrition_notes?.trim() ?? "";
  // Nutrition onboarding is optional and skippable, so most athletes have
  // nothing here — render the section only when there is context to show.
  if (restrictions.length === 0 && notes === "") return null;
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Nutrition</h2>
      {restrictions.length > 0 && (
        <div className={styles.metricRow}>
          <div className={styles.metricLabel}>Dietary restrictions</div>
          <div className={styles.metricValue}>
            <span>{restrictions.join(", ")}</span>
          </div>
        </div>
      )}
      {notes !== "" && (
        <div className={styles.metricRow}>
          <div className={styles.metricLabel}>Notes</div>
          <div className={styles.metricValue}>
            <span>{notes}</span>
          </div>
        </div>
      )}
    </section>
  );
}

// ── Intervals.icu connection ────────────────────────────────

type IntervalsAction = "connect" | "disconnect" | "sync";

type IntervalsSectionProps = {
  action: IntervalsAction | null;
  error: string | null;
  notice: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onSync: () => void;
  status: IntervalsConnectionStatus | null;
};

function IntervalsSection({
  action,
  error,
  notice,
  onConnect,
  onDisconnect,
  onSync,
  status,
}: IntervalsSectionProps): JSX.Element {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Intervals.icu</h2>
      <div className={styles.connectionPanel}>
        <IntervalsMessages error={error} notice={notice} />
        <IntervalsStatusView
          action={action}
          onConnect={onConnect}
          onDisconnect={onDisconnect}
          onSync={onSync}
          status={status}
        />
      </div>
    </section>
  );
}

function IntervalsMessages({
  error,
  notice,
}: Pick<IntervalsSectionProps, "error" | "notice">): JSX.Element {
  return (
    <>
      {notice !== null && <p className={styles.notice}>{notice}</p>}
      {error !== null && <p className={styles.error}>{error}</p>}
    </>
  );
}

type IntervalsStatusViewProps = Pick<
  IntervalsSectionProps,
  "action" | "onConnect" | "onDisconnect" | "onSync" | "status"
>;

function IntervalsStatusView({
  action,
  onConnect,
  onDisconnect,
  onSync,
  status,
}: IntervalsStatusViewProps): JSX.Element {
  if (status === null) {
    return (
      <p className={styles.connectionText}>Loading connection status...</p>
    );
  }
  if (status.connected) {
    return (
      <ConnectedIntervalsStatus
        action={action}
        onDisconnect={onDisconnect}
        onSync={onSync}
        status={status}
      />
    );
  }
  return <DisconnectedIntervalsStatus action={action} onConnect={onConnect} />;
}

function ConnectedIntervalsStatus({
  action,
  onDisconnect,
  onSync,
  status,
}: Pick<IntervalsSectionProps, "action" | "onDisconnect" | "onSync"> & {
  status: IntervalsConnectionStatus;
}): JSX.Element {
  const athleteLabel =
    status.intervals_athlete_name ??
    status.intervals_athlete_id ??
    "Intervals athlete";

  return (
    <>
      <p className={styles.connectionText}>Connected as {athleteLabel}</p>
      {status.intervals_athlete_id !== undefined &&
        status.intervals_athlete_id !== null && (
          <p className={styles.connectionMeta}>{status.intervals_athlete_id}</p>
        )}
      {status.scopes.length > 0 && (
        <p className={styles.connectionMeta}>{status.scopes.join(", ")}</p>
      )}
      <div className={styles.connectionActions}>
        <button
          className={styles.confirmBtn}
          disabled={action !== null}
          onClick={onSync}
          type="button"
        >
          {action === "sync" ? "Syncing..." : "Sync now"}
        </button>
        <button
          className={styles.secondaryBtn}
          disabled={action !== null}
          onClick={onDisconnect}
          type="button"
        >
          {action === "disconnect" ? "Disconnecting..." : "Disconnect"}
        </button>
      </div>
    </>
  );
}

function DisconnectedIntervalsStatus({
  action,
  onConnect,
}: Pick<IntervalsSectionProps, "action" | "onConnect">): JSX.Element {
  return (
    <>
      <p className={styles.connectionText}>Not connected</p>
      <div className={styles.connectionActions}>
        <button
          className={styles.confirmBtn}
          disabled={action !== null}
          onClick={onConnect}
          type="button"
        >
          {action === "connect" ? "Connecting..." : "Connect Intervals.icu"}
        </button>
      </div>
    </>
  );
}

// ── Confirm key type ──────────────────────────────────────────

type ConfirmKey =
  | "cycling_ftp"
  | "max_hr"
  | "run_threshold_pace"
  | "swim_css"
  | "weight";
type ConfirmFn = (key: ConfirmKey, action: () => Promise<void>) => void;

// ── Sport sections ────────────────────────────────────────────

type SportSectionProps = {
  confirmKey: ConfirmKey;
  isConfirming: (k: ConfirmKey) => boolean;
  label: string;
  onConfirm: ConfirmFn;
  sport: string;
  title: string;
  tv: ThresholdValue;
  userId: string;
};

function SportSection({
  title,
  label,
  tv,
  confirmKey,
  sport,
  userId,
  onConfirm,
  isConfirming,
}: SportSectionProps): JSX.Element {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>{title}</h2>
      <MetricRow
        confirming={isConfirming(confirmKey)}
        label={label}
        onConfirm={(): void => {
          onConfirm(confirmKey, () => confirmSportThreshold(userId, sport));
        }}
        tv={tv}
      />
    </section>
  );
}

// ── Physiology section ────────────────────────────────────────

type PhysiologyProps = {
  isConfirming: (k: ConfirmKey) => boolean;
  metrics: FitnessMetrics;
  onConfirm: ConfirmFn;
  userId: string;
};

function PhysiologySection({
  metrics,
  onConfirm,
  isConfirming,
  userId,
}: PhysiologyProps): JSX.Element | null {
  if (metrics.max_hr === undefined && metrics.weight === undefined) return null;
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Physiology</h2>
      {metrics.max_hr !== undefined && (
        <MetricRow
          confirming={isConfirming("max_hr")}
          label="Max HR"
          onConfirm={(): void => {
            onConfirm("max_hr", () => confirmProfileMetric(userId, "max_hr"));
          }}
          tv={metrics.max_hr}
        />
      )}
      {metrics.weight !== undefined && (
        <>
          <MetricRow
            confirming={isConfirming("weight")}
            label="Body weight"
            onConfirm={(): void => {
              onConfirm("weight", () => confirmProfileMetric(userId, "weight"));
            }}
            tv={metrics.weight}
          />
          <p className={styles.weightNote}>
            Used only for watt/kg and fuel calculations. Optional — remove
            anytime via chat.
          </p>
        </>
      )}
    </section>
  );
}

// ── Loaded metrics view ───────────────────────────────────────

type LoadedViewProps = {
  confirming: ConfirmKey | null;
  metrics: FitnessMetrics;
  onConfirm: ConfirmFn;
  profile: AthleteProfile | null;
  userId: string;
};

function LoadedView({
  metrics,
  onConfirm,
  confirming,
  profile,
  userId,
}: LoadedViewProps): JSX.Element {
  const isConfirming = (key: ConfirmKey): boolean => confirming === key;
  return (
    <>
      {metrics.cycling_ftp !== undefined && (
        <SportSection
          confirmKey="cycling_ftp"
          isConfirming={isConfirming}
          label="FTP"
          onConfirm={onConfirm}
          sport="cycling"
          title="Cycling"
          tv={metrics.cycling_ftp}
          userId={userId}
        />
      )}
      {metrics.run_threshold_pace !== undefined && (
        <SportSection
          confirmKey="run_threshold_pace"
          isConfirming={isConfirming}
          label="Threshold pace"
          onConfirm={onConfirm}
          sport="running"
          title="Running"
          tv={metrics.run_threshold_pace}
          userId={userId}
        />
      )}
      {metrics.swim_css !== undefined && (
        <SportSection
          confirmKey="swim_css"
          isConfirming={isConfirming}
          label="CSS"
          onConfirm={onConfirm}
          sport="swimming"
          title="Swimming"
          tv={metrics.swim_css}
          userId={userId}
        />
      )}
      <PhysiologySection
        isConfirming={isConfirming}
        metrics={metrics}
        onConfirm={onConfirm}
        userId={userId}
      />
      <BestTimesSection times={metrics.best_times} />
      <NutritionSection profile={profile} />
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────

export default function ProfilePage(): JSX.Element {
  const [metrics, setMetrics] = useState<FitnessMetrics | null>(null);
  const [profile, setProfile] = useState<AthleteProfile | null>(null);
  const [userId, setUserId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ConfirmKey | null>(null);
  const [intervalsStatus, setIntervalsStatus] =
    useState<IntervalsConnectionStatus | null>(null);
  const [intervalsAction, setIntervalsAction] =
    useState<IntervalsAction | null>(null);
  const [intervalsError, setIntervalsError] = useState<string | null>(null);
  const [intervalsNotice, setIntervalsNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const isCancelled = (): boolean => cancelled;
    const intervalResult = new URLSearchParams(window.location.search).get(
      "intervals",
    );
    if (intervalResult === "connected") {
      setIntervalsNotice("Intervals.icu connected.");
    } else if (intervalResult === "error") {
      setIntervalsError("Intervals.icu authorization was not completed.");
    }
    if (intervalResult !== null) {
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.delete("intervals");
      window.history.replaceState(
        {},
        "",
        `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`,
      );
    }

    async function loadMetricsData(): Promise<void> {
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
    }

    async function loadIntervalsData(): Promise<void> {
      try {
        const nextStatus = await loadIntervalsStatus();
        if (!isCancelled()) {
          setIntervalsStatus(nextStatus);
        }
      } catch (err: unknown) {
        if (!isCancelled()) {
          setIntervalsStatus({ connected: false, scopes: [] });
          setIntervalsError(
            err instanceof Error
              ? err.message
              : "Failed to load Intervals.icu connection status.",
          );
        }
      }
    }

    async function loadProfileData(): Promise<void> {
      await loadMetricsData();
      await loadIntervalsData();
    }

    void loadProfileData();
    return (): void => {
      cancelled = true;
    };
  }, []);

  const confirm = useCallback(
    (key: ConfirmKey, action: () => Promise<void>): void => {
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

  const connectIntervals = useCallback((): void => {
    setIntervalsAction("connect");
    setIntervalsError(null);
    startIntervalsAuthorization()
      .then((redirectUrl) => {
        window.location.assign(redirectUrl);
      })
      .catch((err: unknown) => {
        setIntervalsError(
          err instanceof Error
            ? err.message
            : "Failed to start Intervals.icu authorization.",
        );
      })
      .finally((): void => {
        setIntervalsAction(null);
      });
  }, []);

  const disconnectIntervalsConnection = useCallback((): void => {
    setIntervalsAction("disconnect");
    setIntervalsError(null);
    setIntervalsNotice(null);
    disconnectIntervals()
      .then(setIntervalsStatus)
      .catch((err: unknown) => {
        setIntervalsError(
          err instanceof Error
            ? err.message
            : "Failed to disconnect Intervals.icu.",
        );
      })
      .finally((): void => {
        setIntervalsAction(null);
      });
  }, []);

  const syncIntervalsActivities = useCallback((): void => {
    setIntervalsAction("sync");
    setIntervalsError(null);
    setIntervalsNotice(null);
    syncIntervals()
      .then(({ skipped_duplicates, skipped_invalid, synced }) => {
        const details = [`${skipped_duplicates} already imported`];
        if (skipped_invalid > 0) {
          details.push(`${skipped_invalid} couldn't be imported`);
        }
        setIntervalsNotice(`Synced ${synced} (${details.join("; ")}).`);
      })
      .catch((err: unknown) => {
        setIntervalsError(
          err instanceof Error
            ? err.message
            : "Failed to sync Intervals.icu activities.",
        );
      })
      .finally((): void => {
        setIntervalsAction(null);
      });
  }, []);

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
            action={intervalsAction}
            error={intervalsError}
            notice={intervalsNotice}
            onConnect={connectIntervals}
            onDisconnect={disconnectIntervalsConnection}
            onSync={syncIntervalsActivities}
            status={intervalsStatus}
          />
        )}
        {metrics !== null && userId !== "" && (
          <LoadedView
            confirming={confirming}
            metrics={metrics}
            onConfirm={confirm}
            profile={profile}
            userId={userId}
          />
        )}
      </div>
    </div>
  );
}
