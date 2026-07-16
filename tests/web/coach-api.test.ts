import { afterEach, describe, expect, it, vi } from "vitest";

import {
  disconnectIntervals,
  loadChatMessages,
  loadChatThread,
  loadIntervalsStatus,
  startIntervalsAuthorization,
  syncIntervals,
} from "../../lib/coach-api";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function errorResponse(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_THREAD = {
  attachments_enabled: true,
  next_cursor: null,
  profile_complete: true,
  thread: {
    created_at: "2026-04-04T09:00:00Z",
    id: "thread-1",
    messages: [],
    state: {},
    updated_at: "2026-04-04T09:00:00Z",
    user_id: "athlete-1",
  },
};

const browserToken = (): Response =>
  jsonResponse({ access_token: "token", user_id: "athlete-1" });

describe("loadChatMessages", () => {
  it("rejects malformed paginated message responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ messages: [{ id: "message-1" }], next_cursor: 42 }),
      );

    await expect(loadChatMessages("cursor", fetchMock)).rejects.toThrow();
  });

  it("returns a valid paginated message response", async () => {
    const page = {
      messages: [
        {
          attachments: [],
          content: "",
          created_at: "2026-04-04T09:00:00Z",
          id: "message-1",
          metadata: {},
          role: "user",
          parts: [{ type: "text", text: "hello" }],
          thread_id: "thread-1",
          user_id: "athlete-1",
        },
      ],
      next_cursor: null,
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(jsonResponse(page));

    await expect(loadChatMessages("cursor", fetchMock)).resolves.toEqual(page);
  });

  it("rejects paginated messages missing persisted message fields", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          messages: [
            {
              id: "message-1",
              role: "user",
              parts: [{ type: "text", text: "hello" }],
            },
          ],
          next_cursor: null,
        }),
      );

    await expect(loadChatMessages("cursor", fetchMock)).rejects.toThrow();
  });
});

describe("Intervals.icu helpers", () => {
  it("loads the current user's Intervals connection status", async () => {
    const status = {
      connected: true,
      intervals_athlete_id: "i135168",
      intervals_athlete_name: "Nigel",
      scopes: ["ACTIVITY:READ"],
      connected_at: "2026-07-08T07:00:00Z",
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(browserToken())
      .mockResolvedValueOnce(jsonResponse(status));

    await expect(loadIntervalsStatus(fetchMock)).resolves.toEqual(status);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/intervals/status",
      expect.objectContaining({
        method: "GET",
        headers: expect.any(Headers),
      }),
    );
  });

  it("starts authorization and returns the Intervals redirect URL", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(browserToken())
      .mockResolvedValueOnce(
        jsonResponse({
          redirect_url:
            "https://intervals.icu/oauth/authorize?client_id=client-123",
        }),
      );

    await expect(startIntervalsAuthorization(fetchMock)).resolves.toBe(
      "https://intervals.icu/oauth/authorize?client_id=client-123",
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/intervals/authorize",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("disconnects the current user's Intervals connection", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(browserToken())
      .mockResolvedValueOnce(jsonResponse({ connected: false, scopes: [] }));

    await expect(disconnectIntervals(fetchMock)).resolves.toEqual({
      connected: false,
      scopes: [],
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/intervals/connection",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("syncs recent Intervals activities", async () => {
    const result = {
      activities: [{ id: "activity-1" }],
      skipped_duplicates: 2,
      skipped_invalid: 0,
      synced: 1,
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(browserToken())
      .mockResolvedValueOnce(jsonResponse(result));

    await expect(syncIntervals(30, fetchMock)).resolves.toEqual(result);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/intervals/sync",
      expect.objectContaining({
        body: JSON.stringify({ days: 30 }),
        method: "POST",
      }),
    );
  });

  it("rejects an invalid Intervals sync window before fetching", async () => {
    const fetchMock = vi.fn<typeof fetch>();

    await expect(syncIntervals(91, fetchMock)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("loadChatThread", () => {
  it("rejects malformed thread responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          attachments_enabled: true,
          profile_complete: true,
          thread: { id: "thread-1", messages: [] },
        }),
      );

    await expect(loadChatThread(fetchMock)).rejects.toThrow();
  });

  it("returns a valid thread response", async () => {
    const thread = {
      attachments_enabled: true,
      next_cursor: null,
      profile_complete: true,
      thread: {
        created_at: "2026-04-04T09:00:00Z",
        id: "thread-1",
        messages: [],
        state: {},
        updated_at: "2026-04-04T09:00:00Z",
        user_id: "athlete-1",
      },
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(jsonResponse(thread));

    await expect(loadChatThread(fetchMock)).resolves.toEqual(thread);
  });
});

describe("loadChatThread transient retry", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('retries after a WebKit "Load failed" TypeError and resolves', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn<typeof fetch>()
      // attempt 1: token ok, thread drops with the WebKit abort signature
      .mockResolvedValueOnce(browserToken())
      .mockRejectedValueOnce(new TypeError("Load failed"))
      // attempt 2: token ok, thread ok
      .mockResolvedValueOnce(browserToken())
      .mockResolvedValueOnce(jsonResponse(VALID_THREAD));

    const promise = loadChatThread(fetchMock);
    await vi.advanceTimersByTimeAsync(300);

    await expect(promise).resolves.toEqual(VALID_THREAD);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("retries on a generic TypeError fetch drop", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(browserToken())
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(browserToken())
      .mockResolvedValueOnce(jsonResponse(VALID_THREAD));

    const promise = loadChatThread(fetchMock);
    await vi.advanceTimersByTimeAsync(300);

    await expect(promise).resolves.toEqual(VALID_THREAD);
  });

  it("gives up and rethrows after exhausting retries", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(browserToken())
      .mockRejectedValue(new TypeError("Load failed"));
    // After the initial token call, alternate token/thread; thread always drops.
    fetchMock
      .mockResolvedValueOnce(browserToken())
      .mockRejectedValueOnce(new TypeError("Load failed"))
      .mockResolvedValueOnce(browserToken())
      .mockRejectedValueOnce(new TypeError("Load failed"))
      .mockResolvedValueOnce(browserToken())
      .mockRejectedValueOnce(new TypeError("Load failed"));

    const promise = loadChatThread(fetchMock);
    const assertion = expect(promise).rejects.toThrow("Load failed");
    await vi.advanceTimersByTimeAsync(300 + 900);
    await assertion;
    // 3 attempts (initial + 2 retries) × 2 fetches each = 6 calls.
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it("does not retry when the signal is already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(browserToken())
      .mockRejectedValueOnce(new TypeError("Load failed"));

    await expect(
      loadChatThread(fetchMock, controller.signal),
    ).rejects.toThrow();
    // Initial token + thread only — no retry once aborted.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops retrying when the signal aborts mid-backoff", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(browserToken())
      .mockRejectedValueOnce(new TypeError("Load failed"))
      // These would only be reached if a wasted retry fired after abort.
      .mockResolvedValue(browserToken());

    const promise = loadChatThread(fetchMock, controller.signal);
    const assertion = expect(promise).rejects.toThrow("Load failed");
    // Drain the initial token + thread (thread rejects); execution parks in the
    // 300ms backoff with the first aborted-check already passed.
    await vi.advanceTimersByTimeAsync(100);
    // Abort lands mid-backoff — exercises delay()'s abort listener and the
    // post-delay aborted guard.
    controller.abort();
    await vi.advanceTimersByTimeAsync(300 + 900);
    await assertion;
    // Only the initial token + thread fired; no retry token/thread.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry a non-transient HTTP error", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(browserToken())
      .mockResolvedValueOnce(errorResponse(500, "boom"));

    await expect(loadChatThread(fetchMock)).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
