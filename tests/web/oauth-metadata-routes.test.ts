import type { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { GET as authorizationMetadata } from "../../app/.well-known/oauth-authorization-server/route";
import { GET as protectedResourceMetadata } from "../../app/.well-known/oauth-protected-resource/route";

function nextRequest(url: string): NextRequest {
  return {
    nextUrl: new URL(url)
  } as NextRequest;
}

describe("OAuth discovery metadata routes", () => {
  it("serves the authorization server metadata from the request origin", async () => {
    const response = authorizationMetadata(
      nextRequest("https://preview.example.test/.well-known/oauth-authorization-server")
    );

    await expect(response.json()).resolves.toMatchObject({
      issuer: "https://preview.example.test",
      authorization_endpoint: "https://preview.example.test/api/oauth/authorize",
      token_endpoint: "https://preview.example.test/api/oauth/token",
      revocation_endpoint: "https://preview.example.test/api/oauth/revoke",
      response_types_supported: ["code"],
      grant_types_supported: ["authorization_code", "refresh_token"],
      code_challenge_methods_supported: ["S256"],
      scopes_supported: [
        "metrics:write",
        "plans:read",
        "plans:write",
        "profile:read",
        "profile:write"
      ]
    });
  });

  it("serves the protected resource metadata from the request origin", async () => {
    const response = protectedResourceMetadata(
      nextRequest("https://preview.example.test/.well-known/oauth-protected-resource")
    );

    await expect(response.json()).resolves.toEqual({
      resource: "https://preview.example.test/api/mcp",
      authorization_servers: ["https://preview.example.test"]
    });
  });
});
