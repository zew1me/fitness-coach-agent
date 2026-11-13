import type { ReactNode } from "react";

import type { IntervalsConnectionStatus } from "../../lib/types";

import styles from "./profile.module.css";
import type { IntervalsAction } from "./use-intervals-connection";

type IntervalsSectionProps = {
  action: IntervalsAction | null;
  error: string | null;
  notice: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onSync: () => void;
  status: IntervalsConnectionStatus | null;
};

export function IntervalsSection({
  action,
  error,
  notice,
  onConnect,
  onDisconnect,
  onSync,
  status,
}: IntervalsSectionProps): ReactNode {
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
}: Pick<IntervalsSectionProps, "error" | "notice">): ReactNode {
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
}: IntervalsStatusViewProps): ReactNode {
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
}): ReactNode {
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
}: Pick<IntervalsSectionProps, "action" | "onConnect">): ReactNode {
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
