const supportedScopes = [
  "metrics:write",
  "plans:read",
  "plans:write",
  "profile:read",
  "profile:write"
] as const;

export function buildOAuthAuthorizationMetadata(issuer: string): Record<string, object | string> {
  return {
    issuer,
    authorization_endpoint: `${issuer}/api/oauth/authorize`,
    token_endpoint: `${issuer}/api/oauth/token`,
    registration_endpoint: `${issuer}/api/oauth/register`,
    revocation_endpoint: `${issuer}/api/oauth/revoke`,
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code", "refresh_token"],
    code_challenge_methods_supported: ["S256"],
    scopes_supported: supportedScopes
  };
}

export function buildOAuthProtectedResourceMetadata(issuer: string): Record<string, object | string> {
  return {
    resource: `${issuer}/api/mcp`,
    authorization_servers: [issuer]
  };
}
