import { afterEach, describe, expect, it, vi } from "vitest";

import {
  LeaseAcquisitionError,
  acquireChatTurnLease,
  releaseChatTurnLease,
  renewChatTurnLease,
} from "../../lib/agent/lease-client";

afterEach(() => {
  vi.useRealTimers();
});

describe("acquireChatTurnLease", () => {
  it("posts the lease request and returns the acquired thread state", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ thread_id: "thread-1" }), {
        status: 200,
      }),
    );
    const onLeaseAcquired = vi.fn();

    const state = await acquireChatTurnLease({
      accessToken: "token",
      baseUrl: "http://localhost",
      fetchImpl,
      leaseId: "lease-1",
      onLeaseAcquired,
      ttlSeconds: 900,
    });

    expect(state).toEqual({ thread_id: "thread-1" });
    expect(onLeaseAcquired).toHaveBeenCalledOnce();
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://localhost/api/chat/model-state/lease",
      expect.objectContaining({
        body: JSON.stringify({ lease_id: "lease-1", ttl_seconds: 900 }),
        method: "POST",
      }),
    );
  });

  it("throws a typed acquisition error for conflicts", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("conflict", { status: 409 }));

    try {
      await acquireChatTurnLease({
        accessToken: "token",
        baseUrl: "http://localhost",
        fetchImpl,
        leaseId: "lease-1",
        ttlSeconds: 900,
      });
      throw new Error("Expected lease acquisition to fail.");
    } catch (error) {
      expect(error).toBeInstanceOf(LeaseAcquisitionError);
      expect((error as LeaseAcquisitionError).status).toBe(409);
    }
  });
});

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

describe("renewChatTurnLease", () => {
  it("patches the current lease with its next expiry", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("{}", { status: 200 }));

    await renewChatTurnLease({
      accessToken: "token",
      baseUrl: "http://localhost",
      fetchImpl,
      leaseId: "lease-1",
      ttlSeconds: 60,
    });

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://localhost/api/chat/model-state/lease",
      expect.objectContaining({
        body: JSON.stringify({ lease_id: "lease-1", ttl_seconds: 60 }),
        method: "PATCH",
      }),
    );
  });
});
