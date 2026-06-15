// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadChatThread } from "../../lib/coach-api";
import {
  readLocalChatThread,
  useChatThread,
  writeLocalChatThread,
} from "../../lib/use-chat-thread";

vi.mock("../../lib/coach-api", () => ({
  loadChatThread: vi.fn(),
}));

const loadChatThreadMock = vi.mocked(loadChatThread);

const TOKEN = {
  access_token: "token-abc",
  token_type: "Bearer" as const,
  expires_at: "2099-12-31T00:00:00Z",
  scopes: ["chat"],
  user_id: "user-1",
};

type LocalStorageMock = {
  getItem(_key: string): string | null;
  setItem(_key: string, _value: string): void;
  removeItem(_key: string): void;
  clear(): void;
};

function createLocalStorageMock(): LocalStorageMock {
  let store: Record<string, string> = {};
  return {
    getItem(key: string): string | null {
      return store[key] ?? null;
    },
    setItem(key: string, value: string): void {
      store[key] = value;
    },
    removeItem(key: string): void {
      delete store[key];
    },
    clear(): void {
      store = {};
    },
  };
}

function makeThread(messageCount: number): {
  thread: {
    id: string;
    user_id: string;
    messages: { id: string }[];
  };
  profile_complete: boolean;
} {
  return {
    thread: {
      id: "thread-1",
      user_id: TOKEN.user_id,
      messages: Array.from({ length: messageCount }, (_, idx) => ({
        id: `m-${idx}`,
      })),
    },
    profile_complete: true,
  };
}

let storage: LocalStorageMock;

beforeEach(() => {
  storage = createLocalStorageMock();
  vi.stubGlobal("localStorage", storage);
  // window.localStorage is what the hook reads — wire it to the same mock so
  // jsdom's default (which may not include localStorage) doesn't interfere.
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storage,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  loadChatThreadMock.mockReset();
});

describe("useChatThread", () => {
  it("starts idle when given no token", () => {
    const { result } = renderHook(() => useChatThread(null));
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(loadChatThreadMock).not.toHaveBeenCalled();
  });

  it("loads the remote thread when a token is provided", async () => {
    const thread = makeThread(2);
    loadChatThreadMock.mockResolvedValueOnce(
      thread as unknown as Awaited<ReturnType<typeof loadChatThread>>,
    );

    const { result } = renderHook(() => useChatThread(TOKEN));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.data).toEqual(thread);
    expect(result.current.error).toBeNull();
  });

  it("falls back to local storage when the remote load fails", async () => {
    const localThread = makeThread(3);
    writeLocalChatThread(
      localThread as unknown as Parameters<typeof writeLocalChatThread>[0],
      TOKEN.user_id,
    );
    loadChatThreadMock.mockRejectedValueOnce(new Error("offline"));

    const { result } = renderHook(() => useChatThread(TOKEN));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.data).toEqual(localThread);
    expect(result.current.error).toBeNull();
  });

  it("surfaces the error when remote and local both fail", async () => {
    loadChatThreadMock.mockRejectedValueOnce(new Error("boom"));

    const { result } = renderHook(() => useChatThread(TOKEN));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBe("boom");
  });

  it("writes the loaded thread to local storage when it has messages", async () => {
    const thread = makeThread(1);
    loadChatThreadMock.mockResolvedValueOnce(
      thread as unknown as Awaited<ReturnType<typeof loadChatThread>>,
    );

    renderHook(() => useChatThread(TOKEN));

    await waitFor(() => {
      const restored = readLocalChatThread(TOKEN.user_id);
      expect(restored).toEqual(thread);
    });
  });

  it("does not write an empty thread to local storage", async () => {
    const thread = makeThread(0);
    loadChatThreadMock.mockResolvedValueOnce(
      thread as unknown as Awaited<ReturnType<typeof loadChatThread>>,
    );

    renderHook(() => useChatThread(TOKEN));

    await waitFor(() => {
      expect(loadChatThreadMock).toHaveBeenCalledTimes(1);
    });
    expect(readLocalChatThread(TOKEN.user_id)).toBeNull();
  });
});

describe("readLocalChatThread", () => {
  it("returns null when the stored user_id does not match", () => {
    writeLocalChatThread(makeThread(1) as never, TOKEN.user_id);
    expect(readLocalChatThread("someone-else")).toBeNull();
  });

  it("returns null for malformed JSON without throwing", () => {
    storage.setItem(
      `fitness-coach.local-chat-thread.${TOKEN.user_id}`,
      "{not-json",
    );
    expect(readLocalChatThread(TOKEN.user_id)).toBeNull();
  });
});
