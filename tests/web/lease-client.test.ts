import { describe, expect, it, vi } from "vitest";

import { releaseChatTurnLease } from "../../lib/agent/lease-client";

describe("releaseChatTurnLease", () => {
  it("rejects a non-success response instead of silently ignoring it", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("conflict", { status: 409 }));

    await expect(
      releaseChatTurnLease({
        accessToken: "token",
        baseUrl: "http://localhost",
        fetchImpl,
        leaseId: "lease-1",
      }),
    ).rejects.toThrow("Unable to release chat turn lease (409)");
  });
});
