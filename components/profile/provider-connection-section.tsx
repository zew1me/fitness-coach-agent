import type { JSX, ReactNode } from "react";

import styles from "../../app/profile/profile.module.css";

// One presentational panel shared by the Intervals.icu and Strava connection
// sections so their markup stays in sync. Each provider owns its own state/handlers;
// this component only renders.

export type ProviderConnectionState = {
  athleteLabel: string;
  connected: boolean;
  disconnectPending: boolean;
  metaLines: string[];
};

export type ProviderConnectionSectionProps = {
  /** null while the current action is in flight; otherwise the running action name. */
  action: string | null;
  connectLabel: string;
  connectingLabel: string;
  /** Optional custom connect control (e.g. the official "Connect with Strava" asset). */
  connectButton?: ReactNode;
  disclosure?: ReactNode;
  error: string | null;
  notice: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onSync: () => void;
  /** null while status is loading. */
  state: ProviderConnectionState | null;
  title: string;
};

export function ProviderConnectionSection({
  action,
  connectLabel,
  connectingLabel,
  connectButton,
  disclosure,
  error,
  notice,
  onConnect,
  onDisconnect,
  onSync,
  state,
  title,
}: ProviderConnectionSectionProps): JSX.Element {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>{title}</h2>
      <div className={styles.connectionPanel}>
        {disclosure}
        {notice !== null && <p className={styles.notice}>{notice}</p>}
        {error !== null && <p className={styles.error}>{error}</p>}
        <ProviderStatusView
          action={action}
          connectLabel={connectLabel}
          connectingLabel={connectingLabel}
          connectButton={connectButton}
          onConnect={onConnect}
          onDisconnect={onDisconnect}
          onSync={onSync}
          state={state}
        />
      </div>
    </section>
  );
}

type ProviderStatusViewProps = Pick<
  ProviderConnectionSectionProps,
  | "action"
  | "connectLabel"
  | "connectingLabel"
  | "connectButton"
  | "onConnect"
  | "onDisconnect"
  | "onSync"
  | "state"
>;

function ProviderStatusView({
  action,
  connectLabel,
  connectingLabel,
  connectButton,
  onConnect,
  onDisconnect,
  onSync,
  state,
}: ProviderStatusViewProps): JSX.Element {
  if (state === null) {
    return (
      <p className={styles.connectionText}>Loading connection status...</p>
    );
  }
  if (state.connected) {
    return (
      <ConnectedProviderStatus
        action={action}
        onDisconnect={onDisconnect}
        onSync={onSync}
        state={state}
      />
    );
  }
  return (
    <>
      <p className={styles.connectionText}>Not connected</p>
      <div className={styles.connectionActions}>
        {connectButton ?? (
          <button
            className={styles.confirmBtn}
            disabled={action !== null}
            onClick={onConnect}
            type="button"
          >
            {action === "connect" ? connectingLabel : connectLabel}
          </button>
        )}
      </div>
    </>
  );
}

function ConnectedProviderStatus({
  action,
  onDisconnect,
  onSync,
  state,
}: Pick<ProviderStatusViewProps, "action" | "onDisconnect" | "onSync"> & {
  state: ProviderConnectionState;
}): JSX.Element {
  return (
    <>
      <p className={styles.connectionText}>Connected as {state.athleteLabel}</p>
      {state.metaLines.map((line) => (
        <p className={styles.connectionMeta} key={line}>
          {line}
        </p>
      ))}
      {state.disconnectPending && (
        <p className={styles.error}>
          Disconnect is pending — Strava access could not be revoked yet. Retry
          to complete removal.
        </p>
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
