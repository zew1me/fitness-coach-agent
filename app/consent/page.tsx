"use client";

import { useSearchParams } from "next/navigation";
import type { JSX } from "react";

function formatScopes(scope: string): string[] {
  return scope.split(" ").filter(Boolean);
}

function readConsentParams(searchParams: ReturnType<typeof useSearchParams>): {
  clientId: string;
  codeChallenge: string;
  codeChallengeMethod: string;
  redirectUri: string;
  scope: string;
  state: string;
} {
  return {
    clientId: searchParams.get("client_id") ?? "",
    redirectUri: searchParams.get("redirect_uri") ?? "",
    scope: searchParams.get("scope") ?? "profile:read plans:write metrics:write",
    state: searchParams.get("state") ?? "",
    codeChallenge: searchParams.get("code_challenge") ?? "",
    codeChallengeMethod: searchParams.get("code_challenge_method") ?? "S256"
  };
}

export default function ConsentPage(): JSX.Element {
  const searchParams = useSearchParams();
  const { clientId, redirectUri, scope, state, codeChallenge, codeChallengeMethod } =
    readConsentParams(searchParams);
  const scopes = formatScopes(scope);

  return (
    <main>
      <h1>Connect ChatGPT</h1>
      <p>Approve ChatGPT to read and update training data for the athlete account in this browser.</p>
      <p>Client: {clientId || "Unknown client"}</p>
      <p>Redirect URI: {redirectUri || "Missing redirect URI"}</p>
      <ul>
        {scopes.map((entry) => (
          <li key={entry}>{entry}</li>
        ))}
      </ul>
      <form action="/api/oauth/authorize/decision" method="post">
        <input name="client_id" type="hidden" value={clientId} />
        <input name="redirect_uri" type="hidden" value={redirectUri} />
        <input name="scope" type="hidden" value={scope} />
        <input name="state" type="hidden" value={state} />
        <input name="code_challenge" type="hidden" value={codeChallenge} />
        <input name="code_challenge_method" type="hidden" value={codeChallengeMethod} />
        <button name="decision" type="submit" value="approve">
          Approve access
        </button>
        <button name="decision" type="submit" value="deny">
          Deny
        </button>
      </form>
    </main>
  );
}
