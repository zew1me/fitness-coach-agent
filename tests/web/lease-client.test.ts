import { describe, expect, it, vi } from "vitest";

import { releaseChatTurnLease } from "../../lib/agent/lease-client";

describe("releaseChatTurnLease", () => {
  it("aborts a stalled release request after the helper timeout", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn<typeof fetch>(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
    );

    const release = releaseChatTurnLease({
      accessToken: "token",
      baseUrl: "http://localhost",
      fetchImpl,
      leaseId: "lease-1",
    });
    const assertion = expect(release).rejects.toThrow(/aborted/i);

    await vi.advanceTimersByTimeAsync(2_000);

    await assertion;
    expect(fetchImpl.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
  });

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
