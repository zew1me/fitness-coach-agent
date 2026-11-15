"use client";

import { useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import { Suspense } from "react";

const CLI_CLIENT_ID = "fitness-coach-cli";

function formatScopes(scope: string): string[] {
  return scope.split(" ").filter(Boolean);
}

function clientLabel(clientId: string): string {
  if (clientId === CLI_CLIENT_ID) {
    return `Coach CLI (${CLI_CLIENT_ID})`;
  }
  return clientId || "Unknown client";
}

function CodeEntryForm({ invalid }: { invalid: boolean }): ReactNode {
  return (
    <section className="page-card">
      <h1>Connect the coach CLI</h1>
      <p>Enter the code shown in your terminal after running `coach login`.</p>
      {invalid ? (
        <p role="alert">
          That code is invalid or has expired. Run `coach login` again to get a
          new one.
        </p>
      ) : null}
      <form action="/api/oauth/device" method="get">
        <label htmlFor="user_code">Code</label>
        <input
          id="user_code"
          name="user_code"
          className="input"
          type="text"
          autoFocus
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          placeholder="XXXX-XXXX"
          style={{ textTransform: "uppercase" }}
          required
        />
        <button className="button" type="submit">
          Continue
        </button>
      </form>
    </section>
  );
}

function ConfirmCard({
  userCode,
  clientId,
  scope,
}: {
  userCode: string;
  clientId: string;
  scope: string;
}): ReactNode {
  return (
    <section className="page-card">
      <h1>Approve the coach CLI</h1>
      <p>Confirm this code matches the one in your terminal:</p>
      <p>
        <strong style={{ fontSize: "2rem", letterSpacing: "0.1em" }}>
          {userCode}
        </strong>
      </p>
      <p>Client: {clientLabel(clientId)}</p>
      <ul className="scope-list">
        {formatScopes(scope).map((entry) => (
          <li key={entry}>{entry}</li>
        ))}
      </ul>
      <p>
        Only approve if you started `coach login` yourself and this code matches
        your terminal.
      </p>
      <form action="/api/oauth/device/decision" method="post">
        <input name="user_code" type="hidden" value={userCode} />
        <input name="client_id" type="hidden" value={clientId} />
        <input name="scope" type="hidden" value={scope} />
        <button
          className="button button-secondary"
          name="decision"
          type="submit"
          value="approve"
        >
          Approve
        </button>
        <button
          className="button button-ghost"
          name="decision"
          type="submit"
          value="deny"
        >
          Deny
        </button>
      </form>
    </section>
  );
}

function MessageCard({
  title,
  body,
}: {
  title: string;
  body: string;
}): ReactNode {
  return (
    <section className="page-card">
      <h1>{title}</h1>
      <p>{body}</p>
    </section>
  );
}

function DevicePageContent(): ReactNode {
  const searchParams = useSearchParams();
  const status = searchParams.get("status");
  const step = searchParams.get("step");
  const error = searchParams.get("error");

  let card: ReactNode;
  if (status === "approved") {
    card = (
      <MessageCard
        title="CLI connected"
        body="CLI connected. You can close this tab and return to your terminal."
      />
    );
  } else if (status === "denied") {
    card = (
      <MessageCard
        title="Request denied"
        body="Request denied. Nothing was connected."
      />
    );
  } else if (step === "confirm" && searchParams.get("user_code")) {
    card = (
      <ConfirmCard
        userCode={searchParams.get("user_code") ?? ""}
        clientId={searchParams.get("client_id") ?? ""}
        scope={searchParams.get("scope") ?? ""}
      />
    );
  } else {
    card = <CodeEntryForm invalid={error === "invalid_code"} />;
  }

  return <main className="page">{card}</main>;
}

export default function DevicePage(): ReactNode {
  return (
    <Suspense
      fallback={
        <main className="page">
          <section className="page-card">
            <p>Loading…</p>
          </section>
        </main>
      }
    >
      <DevicePageContent />
    </Suspense>
  );
}
