"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { JSX } from "react";

import {
  ProviderConnectionSection,
  type ProviderConnectionState,
} from "../../components/profile/provider-connection-section";
import { StatusCard } from "../../components/status-card";
import {
  confirmProfileMetric,
  confirmSportThreshold,
  disconnectIntervals,
  disconnectStrava,
  fetchBrowserToken,
  loadFitnessMetrics,
  loadIntervalsStatus,
  loadStravaStatus,
  startIntervalsAuthorization,
  startStravaAuthorization,
  syncIntervals,
  syncStrava,
} from "../../lib/coach-api";
import type {
  BestTime,
  FitnessMetrics,
  IntervalsConnectionStatus,
  StravaConnectionStatus,
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

// ── Provider connection adapters ─────────────────────────────

type ProviderAction = "connect" | "disconnect" | "sync";

type IntervalsSectionProps = {
  action: ProviderAction | null;
  error: string | null;
  notice: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onSync: () => void;
  status: IntervalsConnectionStatus | null;
};

function toIntervalsState(
  status: IntervalsConnectionStatus | null,
): ProviderConnectionState | null {
  if (status === null) return null;
  if (!status.connected) {
    return {
      athleteLabel: "",
      connected: false,
      disconnectPending: false,
      metaLines: [],
    };
  }
  const metaLines: string[] = [];
  if (
    status.intervals_athlete_id !== undefined &&
    status.intervals_athlete_id !== null
  ) {
    metaLines.push(status.intervals_athlete_id);
  }
  if (status.scopes.length > 0) {
    metaLines.push(status.scopes.join(", "));
  }
  return {
    athleteLabel:
      status.intervals_athlete_name ??
      status.intervals_athlete_id ??
      "Intervals athlete",
    connected: true,
    disconnectPending: false,
    metaLines,
  };
}

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
    <ProviderConnectionSection
      action={action}
      connectLabel="Connect Intervals.icu"
      connectingLabel="Connecting..."
      error={error}
      notice={notice}
      onConnect={onConnect}
      onDisconnect={onDisconnect}
      onSync={onSync}
      state={toIntervalsState(status)}
      title="Intervals.icu"
    />
  );
}

type StravaSectionProps = {
  action: ProviderAction | null;
  error: string | null;
  notice: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onSync: () => void;
  status: StravaConnectionStatus | null;
};

function stravaMetaLines(status: StravaConnectionStatus): string[] {
  const metaLines: string[] = [];
  const athleteId = status.strava_athlete_id;
  if (athleteId !== undefined && athleteId !== null) {
    metaLines.push(`Athlete ${athleteId}`);
  }
  if (status.scopes.length > 0) {
    metaLines.push(status.scopes.join(", "));
  }
  if (status.last_sync_at) {
    metaLines.push(
      `Last sync ${new Date(status.last_sync_at).toLocaleString()}`,
    );
  }
  return metaLines;
}

function stravaAthleteLabel(status: StravaConnectionStatus): string {
  const athleteId = status.strava_athlete_id;
  const idLabel =
    athleteId !== undefined && athleteId !== null ? String(athleteId) : null;
  return status.strava_athlete_name ?? idLabel ?? "Strava athlete";
}

function toStravaState(
  status: StravaConnectionStatus | null,
): ProviderConnectionState | null {
  if (status === null) return null;
  const disconnectPending = Boolean(status.disconnect_pending);
  if (!status.connected) {
    return {
      athleteLabel: "",
      connected: false,
      disconnectPending,
      metaLines: [],
    };
  }
  return {
    athleteLabel: stravaAthleteLabel(status),
    connected: true,
    disconnectPending,
    metaLines: stravaMetaLines(status),
  };
}

function StravaDisclosure(): JSX.Element {
  return (
    <p className={styles.connectionMeta}>
      Imports activity summaries only (sport, time, distance, elevation, heart
      rate, power, cadence) — never GPS, maps, or photos. Disconnecting revokes
      Strava access and deletes imported Strava activities. See our{" "}
      <Link href="/privacy">privacy policy</Link>.
    </p>
  );
}

function StravaSection({
  action,
  error,
  notice,
  onConnect,
  onDisconnect,
  onSync,
  status,
}: StravaSectionProps): JSX.Element {
  return (
    <ProviderConnectionSection
      action={action}
      connectLabel="Connect with Strava"
      connectingLabel="Connecting..."
      disclosure={<StravaDisclosure />}
      error={error}
      notice={notice}
      onConnect={onConnect}
      onDisconnect={onDisconnect}
      onSync={onSync}
      state={toStravaState(status)}
      title="Strava"
    />
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
  userId: string;
};

function LoadedView({
  metrics,
  onConfirm,
  confirming,
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
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────

export default function ProfilePage(): JSX.Element {
  const [metrics, setMetrics] = useState<FitnessMetrics | null>(null);
  const [userId, setUserId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ConfirmKey | null>(null);
  const [intervalsStatus, setIntervalsStatus] =
    useState<IntervalsConnectionStatus | null>(null);
  const [intervalsAction, setIntervalsAction] = useState<ProviderAction | null>(
    null,
  );
  const [intervalsError, setIntervalsError] = useState<string | null>(null);
  const [intervalsNotice, setIntervalsNotice] = useState<string | null>(null);
  const [stravaStatus, setStravaStatus] =
    useState<StravaConnectionStatus | null>(null);
  const [stravaAction, setStravaAction] = useState<ProviderAction | null>(null);
  const [stravaError, setStravaError] = useState<string | null>(null);
  const [stravaNotice, setStravaNotice] = useState<string | null>(null);

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
    const stravaResult = new URLSearchParams(window.location.search).get(
      "strava",
    );
    if (stravaResult === "connected") {
      setStravaNotice("Strava connected.");
    } else if (stravaResult === "scope_error") {
      setStravaError(
        "Strava did not grant activity access. Reconnect and allow activity read.",
      );
    } else if (stravaResult === "error") {
      setStravaError("Strava authorization was not completed.");
    }
    if (intervalResult !== null || stravaResult !== null) {
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.delete("intervals");
      nextUrl.searchParams.delete("strava");
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

        const nextMetrics = await loadFitnessMetrics(token.user_id);
        if (!isCancelled()) {
          setMetrics(nextMetrics);
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

    async function loadStravaData(): Promise<void> {
      try {
        const nextStatus = await loadStravaStatus();
        if (!isCancelled()) {
          setStravaStatus(nextStatus);
        }
      } catch (err: unknown) {
        if (!isCancelled()) {
          setStravaStatus({ connected: false, scopes: [] });
          setStravaError(
            err instanceof Error
              ? err.message
              : "Failed to load Strava connection status.",
          );
        }
      }
    }

    async function loadProfileData(): Promise<void> {
      await loadMetricsData();
      await loadIntervalsData();
      await loadStravaData();
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

  const connectStrava = useCallback((): void => {
    setStravaAction("connect");
    setStravaError(null);
    startStravaAuthorization()
      .then((redirectUrl) => {
        window.location.assign(redirectUrl);
      })
      .catch((err: unknown) => {
        setStravaError(
          err instanceof Error
            ? err.message
            : "Failed to start Strava authorization.",
        );
      })
      .finally((): void => {
        setStravaAction(null);
      });
  }, []);

  const disconnectStravaConnection = useCallback((): void => {
    setStravaAction("disconnect");
    setStravaError(null);
    setStravaNotice(null);
    disconnectStrava()
      .then((status) => {
        setStravaStatus(status);
        if (status.disconnect_pending) {
          setStravaError(
            "Strava access could not be revoked yet. Please retry disconnect.",
          );
        } else {
          setStravaNotice(
            `Disconnected from Strava. Deleted ${status.deleted_activities} imported ${status.deleted_activities === 1 ? "activity" : "activities"}.`,
          );
        }
      })
      .catch((err: unknown) => {
        setStravaError(
          err instanceof Error ? err.message : "Failed to disconnect Strava.",
        );
      })
      .finally((): void => {
        setStravaAction(null);
      });
  }, []);

  const syncStravaActivities = useCallback((): void => {
    setStravaAction("sync");
    setStravaError(null);
    setStravaNotice(null);
    syncStrava()
      .then(({ skipped_duplicates, skipped_invalid, synced }) => {
        const details = [`${skipped_duplicates} already imported`];
        if (skipped_invalid > 0) {
          details.push(`${skipped_invalid} couldn't be imported`);
        }
        setStravaNotice(`Synced ${synced} (${details.join("; ")}).`);
      })
      .catch((err: unknown) => {
        setStravaError(
          err instanceof Error
            ? err.message
            : "Failed to sync Strava activities.",
        );
      })
      .finally((): void => {
        setStravaAction(null);
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
          <>
            <IntervalsSection
              action={intervalsAction}
              error={intervalsError}
              notice={intervalsNotice}
              onConnect={connectIntervals}
              onDisconnect={disconnectIntervalsConnection}
              onSync={syncIntervalsActivities}
              status={intervalsStatus}
            />
            <StravaSection
              action={stravaAction}
              error={stravaError}
              notice={stravaNotice}
              onConnect={connectStrava}
              onDisconnect={disconnectStravaConnection}
              onSync={syncStravaActivities}
              status={stravaStatus}
            />
          </>
        )}
        {metrics !== null && userId !== "" && (
          <LoadedView
            confirming={confirming}
            metrics={metrics}
            onConfirm={confirm}
            userId={userId}
          />
        )}
      </div>
    </div>
  );
}
