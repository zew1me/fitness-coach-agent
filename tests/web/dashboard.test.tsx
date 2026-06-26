// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const chatMocks = vi.hoisted(() => {
  const messages: unknown[] = [];
  const sendMessage = vi.fn(() => Promise.resolve());
  const setMessages = vi.fn();
  const useChat = vi.fn(() => ({
    addToolApprovalResponse: vi.fn(),
    addToolOutput: vi.fn(),
    addToolResult: vi.fn(),
    clearError: vi.fn(),
    error: undefined,
    id: "test-chat",
    messages,
    regenerate: vi.fn(),
    resumeStream: vi.fn(),
    sendMessage,
    setMessages,
    status: "ready",
    stop: vi.fn(),
  }));

  return { messages, sendMessage, setMessages, useChat };
});

vi.mock("@ai-sdk/react", () => ({
  useChat: chatMocks.useChat,
}));

import { CoachChat } from "../../components/coach-chat";

const originalFetch = globalThis.fetch;
const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const localStorageStore = new Map<string, string>();
const localStorageMock = {
  clear: (): void => {
    localStorageStore.clear();
  },
  getItem: (key: string): string | null => localStorageStore.get(key) ?? null,
  key: (index: number): string | null =>
    Array.from(localStorageStore.keys())[index] ?? null,
  get length(): number {
    return localStorageStore.size;
  },
  removeItem: (key: string): void => {
    localStorageStore.delete(key);
  },
  setItem: (key: string, value: string): void => {
    localStorageStore.set(key, value);
  },
};

beforeEach(() => {
  chatMocks.messages.splice(0);
  chatMocks.sendMessage.mockClear();
  chatMocks.setMessages.mockClear();
  chatMocks.useChat.mockClear();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: localStorageMock,
  });
  vi.stubGlobal("localStorage", localStorageMock);
  // ThemeSwitcher (rendered in the account menu) compiles JSX to a global
  // `React` reference under vitest's classic runtime, so provide it.
  vi.stubGlobal("React", React);
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList);
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  localStorageMock.clear();
  vi.useRealTimers();
});

describe("CoachChat", () => {
  it("shows a login prompt when the browser session cannot mint a token", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        new Response("No browser session cookie is present.", { status: 401 }),
      ),
    ) as unknown as typeof fetch;

    render(<CoachChat />);

    await screen.findByText(/Continue with magic link/i);
    expect(
      screen.getByText(/Sign in to start your coaching chat/i),
    ).toBeTruthy();
  });

  it("shows a bounded fallback error when the bootstrap request returns HTML", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        new Response("<!DOCTYPE html><html><body><h1>404</h1></body></html>", {
          status: 404,
          headers: {
            "content-type": "text/html; charset=utf-8",
          },
        }),
      ),
    ) as unknown as typeof fetch;

    render(<CoachChat />);

    await screen.findByText(/Continue with magic link/i);
    expect(
      screen.getByText(/Sign in to start your coaching chat/i),
    ).toBeTruthy();
    expect(screen.queryByText(/<!DOCTYPE html>/i)).toBeNull();
  });

  it("uses the playful unavailable state when the signed-in thread load fails", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read"],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({ detail: "Chat backend still warming up." }),
            {
              status: 503,
              headers: { "content-type": "application/json" },
            },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Sorry, we're out running./i);
    expect(
      screen.getByText(/We'll be back soon. You've got this./i),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry/i })).toBeTruthy();
  });

  it("loads the persisted coach thread after the browser-token bridge succeeds", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/What should we work on next/i);
    expect(screen.getByText(/Welcome back coach-side/i)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/thread",
      expect.objectContaining({
        method: "GET",
        credentials: "include",
      }),
    );
  });

  it("renders a bounded recent message buffer until older history is requested", async () => {
    const threadMessages = Array.from({ length: 70 }, (_, index) => ({
      id: `message-${index}`,
      attachments: [],
      content: `History message ${index}`,
      created_at: `2026-04-04T09:${String(index % 60).padStart(2, "0")}:00Z`,
      metadata: {},
      role: index % 2 === 0 ? "user" : "assistant",
      thread_id: "thread-1",
      user_id: "athlete-1",
    }));
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: threadMessages,
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText("History message 69");
    expect(screen.queryByText("History message 0")).toBeNull();
    expect(
      screen.getByRole("button", { name: /Show 10 older messages/i }),
    ).toBeTruthy();

    const showOlder = screen.getByRole("button", {
      name: /Show 10 older messages/i,
    });
    await act(async () => {
      fireEvent.click(showOlder);
      await Promise.resolve();
    });

    expect(await screen.findByText("History message 0")).toBeTruthy();
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /Show older messages/i }),
      ).toBeNull();
    });
  });

  it("does not prepend the same older page twice while pagination is in flight", async () => {
    const threadMessages = Array.from({ length: 5 }, (_, index) => ({
      id: `message-${index}`,
      attachments: [],
      content: `Recent message ${index}`,
      created_at: `2026-04-04T09:0${index}:00Z`,
      metadata: {},
      role: index % 2 === 0 ? "user" : "assistant",
      thread_id: "thread-1",
      user_id: "athlete-1",
    }));
    let resolveOlder:
      | ((_value: Response | PromiseLike<Response>) => void)
      | undefined;
    const olderResponse = new Promise<Response>((resolve) => {
      resolveOlder = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              next_cursor: "cursor-1",
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: threadMessages,
              },
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/messages?before=cursor-1&limit=50") {
        return olderResponse;
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const showOlder = await screen.findByRole("button", {
      name: /Show older messages/i,
    });
    fireEvent.click(showOlder);
    fireEvent.click(showOlder);
    resolveOlder?.(
      new Response(
        JSON.stringify({
          messages: [
            {
              id: "older-1",
              attachments: [],
              content: "Older page message",
              created_at: "2026-04-04T08:59:00Z",
              metadata: {},
              parts: [{ type: "text", text: "Older page message" }],
              role: "assistant",
              thread_id: "thread-1",
              user_id: "athlete-1",
            },
          ],
          next_cursor: null,
        }),
        { status: 200 },
      ),
    );

    await screen.findByText("Older page message");

    expect(
      screen.getAllByText("Older page message", { exact: true }),
    ).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter(
        ([url]) =>
          String(url) === "/api/chat/messages?before=cursor-1&limit=50",
      ),
    ).toHaveLength(1);
  });

  it("does not reveal stale older-page counts when the thread cursor changes before the page resolves", async () => {
    const initialMessages = Array.from({ length: 50 }, (_, index) => ({
      id: `message-${index}`,
      attachments: [],
      content: `Recent message ${index}`,
      created_at: new Date(Date.UTC(2026, 3, 4, 9, index, 0)).toISOString(),
      metadata: {},
      role: index % 2 === 0 ? "user" : "assistant",
      thread_id: "thread-1",
      user_id: "athlete-1",
    }));
    const refreshedMessages = Array.from({ length: 70 }, (_, index) => ({
      id: `refreshed-${index}`,
      attachments: [],
      content: `Refreshed message ${index}`,
      created_at: new Date(Date.UTC(2026, 3, 4, 10, index, 0)).toISOString(),
      metadata: {},
      role: index % 2 === 0 ? "user" : "assistant",
      thread_id: "thread-1",
      user_id: "athlete-1",
    }));
    let threadLoads = 0;
    let resolveOlder:
      | ((_value: Response | PromiseLike<Response>) => void)
      | undefined;
    const olderResponse = new Promise<Response>((resolve) => {
      resolveOlder = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        threadLoads += 1;
        const messages =
          threadLoads === 1 ? initialMessages : refreshedMessages;
        const nextCursor = threadLoads === 1 ? "cursor-1" : "cursor-2";
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              next_cursor: nextCursor,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages,
              },
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/messages?before=cursor-1&limit=50") {
        return olderResponse;
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const showOlder = await screen.findByRole("button", {
      name: /^Show older messages$/i,
    });
    fireEvent.click(showOlder);

    const input = await screen.findByPlaceholderText(/Ask your coach/i);
    fireEvent.change(input, { target: { value: "I ran easy today." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    await act(async () => {
      resolveOlder?.(
        new Response(
          JSON.stringify({
            messages: [
              {
                id: "stale-older-1",
                attachments: [],
                content: "Stale older page message",
                created_at: "2026-04-04T08:59:00Z",
                metadata: {},
                parts: [{ type: "text", text: "Stale older page message" }],
                role: "assistant",
                thread_id: "thread-1",
                user_id: "athlete-1",
              },
            ],
            next_cursor: null,
          }),
          { status: 200 },
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Show 10 older messages/i }),
      ).toBeTruthy();
    });
    expect(screen.queryByText("Stale older page message")).toBeNull();
  });

  it("restores locally persisted chat history when the local thread endpoint is unavailable", async () => {
    localStorage.setItem(
      "fitness-coach.local-chat-thread.athlete-1",
      JSON.stringify({
        attachments_enabled: false,
        profile_complete: true,
        thread: {
          id: "thread-1",
          user_id: "athlete-1",
          state: {},
          created_at: "2026-04-04T09:00:00Z",
          updated_at: "2026-04-04T09:00:00Z",
          messages: [
            {
              id: "local-message-1",
              attachments: [],
              content: "Saved local training note.",
              created_at: "2026-04-04T09:00:00Z",
              metadata: {},
              role: "user",
              thread_id: "thread-1",
              user_id: "athlete-1",
            },
          ],
        },
      }),
    );
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({ detail: "Local backend is restarting." }),
            {
              status: 503,
              headers: { "content-type": "application/json" },
            },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText("Saved local training note.");
    expect(screen.queryByText(/Sorry, we're out running/i)).toBeNull();
  });

  it("prefers the remote chat thread over a longer stale local cache", async () => {
    localStorage.setItem(
      "fitness-coach.local-chat-thread.athlete-1",
      JSON.stringify({
        attachments_enabled: false,
        profile_complete: true,
        thread: {
          id: "thread-1",
          user_id: "athlete-1",
          state: {},
          created_at: "2026-04-04T09:00:00Z",
          updated_at: "2026-04-04T09:01:00Z",
          messages: [
            {
              id: "remote-message-1",
              attachments: [],
              content: "Canonical server note.",
              created_at: "2026-04-04T09:00:00Z",
              metadata: {},
              role: "user",
              thread_id: "thread-1",
              user_id: "athlete-1",
            },
            {
              id: "stale-local-duplicate",
              attachments: [],
              content: "Stale duplicate note.",
              created_at: "2026-04-04T09:00:01Z",
              metadata: {},
              role: "user",
              thread_id: "thread-1",
              user_id: "athlete-1",
            },
          ],
        },
      }),
    );
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:02:00Z",
                messages: [
                  {
                    id: "remote-message-1",
                    attachments: [],
                    content: "Canonical server note.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {},
                    role: "user",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText("Canonical server note.");
    expect(screen.queryByText("Stale duplicate note.")).toBeNull();

    await waitFor(() => {
      const cachedRaw = localStorage.getItem(
        "fitness-coach.local-chat-thread.athlete-1",
      );
      expect(cachedRaw).not.toBeNull();
      const cached = JSON.parse(cachedRaw ?? "") as {
        thread: { messages: Array<{ content: string; id: string }> };
      };
      expect(cached.thread.messages).toEqual([
        expect.objectContaining({
          content: "Canonical server note.",
          id: "remote-message-1",
        }),
      ]);
    });
  });

  it("does not write transient live chat messages into local storage", async () => {
    chatMocks.messages.push({
      id: "live-message-1",
      parts: [{ text: "Transient optimistic note.", type: "text" }],
      role: "user",
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:02:00Z",
                messages: [
                  {
                    id: "remote-message-1",
                    attachments: [],
                    content: "Canonical server note.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {},
                    role: "user",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText("Canonical server note.");
    await waitFor(() => {
      const cachedRaw = localStorage.getItem(
        "fitness-coach.local-chat-thread.athlete-1",
      );
      expect(cachedRaw).not.toBeNull();
      const cached = JSON.parse(cachedRaw ?? "") as {
        thread: { messages: Array<{ content: string; id: string }> };
      };
      expect(cached.thread.messages).toEqual([
        expect.objectContaining({
          content: "Canonical server note.",
          id: "remote-message-1",
        }),
      ]);
    });
  });

  it("uses friendly signed-in copy instead of showing a raw user id", async () => {
    const userId = "aa687ce1-5189-4c28-bf24-e8b1574ebc5b";
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: userId,
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: false,
              thread: {
                id: "thread-1",
                user_id: userId,
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: userId,
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Building your athlete profile/i);
    expect(screen.queryByText(userId)).toBeNull();
  });

  it("frames the first onboarding welcome around sport and coaching goal", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: false,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {
                  pending_profile_field: "goals",
                },
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content:
                      "Welcome. Let's start with just two things: what sport or sports are you training for, and what would you like coaching around?",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByRole("heading", {
      name: /Start with your sport and goal/i,
    });
    expect(screen.getByText(/A short answer is enough/i)).toBeTruthy();
    expect(
      screen.getByText(/what sport or sports are you training for/i),
    ).toBeTruthy();
    expect(
      screen.getByPlaceholderText(/Tell your coach your sport and goal/i),
    ).toBeTruthy();
    expect(screen.queryByText(/What should we work on next/i)).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Generate next plan/i }),
    ).toBeNull();
  });

  it("prefills the onboarding composer when a starter prompt is chosen", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: false,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {
                  pending_profile_field: "goals",
                },
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content:
                      "Welcome. Let's start with just two things: what sport or sports are you training for, and what would you like coaching around?",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const starter = await screen.findByRole("button", {
      name: /Running base and consistency/i,
    });
    fireEvent.click(starter);

    await waitFor(() => {
      expect(
        (
          screen.getByPlaceholderText(
            /Tell your coach your sport and goal/i,
          ) as HTMLTextAreaElement
        ).value,
      ).toBe("I'm training for running and want help building consistency.");
    });
  });

  it("opens profile preferences from the account menu without raw id editing", async () => {
    const userId = "aa687ce1-5189-4c28-bf24-e8b1574ebc5b";
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: userId,
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: false,
              thread: {
                id: "thread-1",
                user_id: userId,
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: userId,
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/profile") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              coaching_state: "active",
              display_name: "Riley",
              primary_sports: ["running", "cycling"],
              user_id: userId,
              weekly_available_hours: 7.5,
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Building your athlete profile/i);
    expect(screen.queryByText(new RegExp(userId))).toBeNull();
    expect(screen.queryByRole("link", { name: /Switch login/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Account menu/i }));

    await screen.findByRole("menu", { name: /Account/i });
    expect(screen.getByRole("menuitem", { name: /Sign out/i })).toBeTruthy();
    const signOutButton = screen.getByRole("menuitem", { name: /Sign out/i });
    expect(signOutButton.getAttribute("type")).toBe("submit");
    expect(signOutButton.closest("form")?.getAttribute("action")).toBe(
      "/api/oauth/browser-session/logout",
    );
    expect(signOutButton.closest("form")?.getAttribute("method")).toBe("post");
    fireEvent.click(screen.getByRole("menuitem", { name: /Profile/i }));

    await screen.findByRole("heading", { name: /Profile/i });
    expect(screen.getByLabelText(/Display name/i)).toBeTruthy();
    expect(screen.getByLabelText(/Sports/i)).toBeTruthy();
    expect(screen.getByLabelText(/Weekly training hours/i)).toBeTruthy();
    expect(screen.queryByLabelText(/User ID/i)).toBeNull();
    expect(screen.queryByLabelText(/FTP/i)).toBeNull();
  });

  it("exports the loaded coaching history as JSONL from the account menu", async () => {
    const objectUrls: Blob[] = [];
    const createdLinks: HTMLAnchorElement[] = [];
    const originalCreateElement = document.createElement.bind(document);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const createElementSpy = vi
      .spyOn(document, "createElement")
      .mockImplementation((tagName, options) => {
        const element = originalCreateElement(tagName, options);
        if (tagName.toLowerCase() === "a") {
          vi.spyOn(element, "click").mockImplementation(() => undefined);
          createdLinks.push(element as HTMLAnchorElement);
        }
        return element;
      });
    const createObjectUrlSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockImplementation((blob) => {
        expect(blob).toBeInstanceOf(Blob);
        objectUrls.push(blob as Blob);
        return "blob:coaching-history";
      });
    const revokeObjectUrlSpy = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => undefined);

    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:05:00Z",
                messages: [
                  {
                    id: "message-user",
                    attachments: [
                      {
                        content_type: "image/png",
                        filename: "ride.png",
                        object_key: "users/athlete-1/chat-attachment/ride.png",
                        public_url: "https://cdn.example.com/ride.png",
                      },
                    ],
                    content: "Here is today's ride.",
                    created_at: "2026-04-04T09:01:00Z",
                    metadata: {},
                    role: "user",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                  {
                    id: "message-assistant",
                    attachments: [],
                    content: "Nice aerobic work.",
                    created_at: "2026-04-04T09:02:00Z",
                    metadata: { message_kind: "coach_reply" },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Here is today's ride/i);
    fireEvent.click(screen.getByRole("button", { name: /Account menu/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Export JSONL/i }));

    expect(createObjectUrlSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectUrlSpy).toHaveBeenCalledWith("blob:coaching-history");
    const downloadLink = createdLinks.find(
      (link) => link.href === "blob:coaching-history",
    );
    expect(downloadLink?.download).toMatch(
      /^coaching-history-\d{4}-\d{2}-\d{2}\.jsonl$/,
    );

    const blobText = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(String(reader.result)));
      reader.addEventListener("error", () => reject(reader.error));
      reader.readAsText(objectUrls[0]!);
    });
    const lines = blobText.trim().split("\n");
    expect(lines).toHaveLength(2);
    expect(JSON.parse(lines[0]!)).toMatchObject({
      id: "message-user",
      role: "user",
      content: "Here is today's ride.",
      attachments: [{ public_url: "https://cdn.example.com/ride.png" }],
    });
    expect(JSON.parse(lines[1]!)).toMatchObject({
      id: "message-assistant",
      role: "assistant",
      content: "Nice aerobic work.",
    });

    createElementSpy.mockRestore();
  });

  it("sends composer messages through the AI SDK useChat hook", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const input = await screen.findByPlaceholderText(/Ask your coach/i);
    fireEvent.change(input, { target: { value: "I ran easy today." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => {
      expect(chatMocks.sendMessage).toHaveBeenCalledWith({
        id: expect.stringMatching(uuidPattern),
        parts: [{ text: "I ran easy today.", type: "text" }],
      });
    });
    expect(fetchMock).not.toHaveBeenCalledWith("/api/chat", expect.anything());
  });

  it("ignores rapid duplicate send events while the first send is in flight", async () => {
    let resolveSend: (() => void) | null = null;
    chatMocks.sendMessage.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveSend = resolve;
        }),
    );
    const threadResponse = {
      attachments_enabled: false,
      profile_complete: true,
      thread: {
        id: "thread-1",
        user_id: "athlete-1",
        state: {},
        created_at: "2026-04-04T09:00:00Z",
        updated_at: "2026-04-04T09:00:00Z",
        messages: [
          {
            id: "message-1",
            attachments: [],
            content: "Welcome back coach-side.",
            created_at: "2026-04-04T09:00:00Z",
            metadata: {
              message_kind: "welcome",
            },
            role: "assistant",
            thread_id: "thread-1",
            user_id: "athlete-1",
          },
        ],
      },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read"],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(JSON.stringify(threadResponse), { status: 200 }),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const input = await screen.findByPlaceholderText(/Ask your coach/i);
    fireEvent.change(input, { target: { value: "I ran easy today." } });
    const sendButton = screen.getByRole("button", { name: /^Send$/i });
    fireEvent.click(sendButton);
    fireEvent.click(sendButton);

    expect(chatMocks.sendMessage).toHaveBeenCalledOnce();

    await act(() => Promise.resolve(resolveSend?.()));
  });

  it("shows a thread sync status while reloading after a sent message", async () => {
    let resolveThreadReload: (() => void) | null = null;
    let threadRequestCount = 0;
    const threadResponse = {
      attachments_enabled: false,
      profile_complete: true,
      thread: {
        id: "thread-1",
        user_id: "athlete-1",
        state: {},
        created_at: "2026-04-04T09:00:00Z",
        updated_at: "2026-04-04T09:00:00Z",
        messages: [
          {
            id: "message-1",
            attachments: [],
            content: "Welcome back coach-side.",
            created_at: "2026-04-04T09:00:00Z",
            metadata: {
              message_kind: "welcome",
            },
            role: "assistant",
            thread_id: "thread-1",
            user_id: "athlete-1",
          },
        ],
      },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        threadRequestCount += 1;
        if (threadRequestCount === 1) {
          return Promise.resolve(
            new Response(JSON.stringify(threadResponse), { status: 200 }),
          );
        }

        return new Promise<Response>((resolve) => {
          resolveThreadReload = (): void => {
            resolve(
              new Response(JSON.stringify(threadResponse), { status: 200 }),
            );
          };
        });
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const input = await screen.findByPlaceholderText(/Ask your coach/i);
    fireEvent.change(input, { target: { value: "I ran easy today." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => {
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: /^Syncing$/i })
          .disabled,
      ).toBe(true);
    });
    expect(screen.getByText("Syncing coach chat...")).toBeTruthy();

    await act(() => Promise.resolve(resolveThreadReload?.()));
  });

  it("shows a rotating wait status while a message is processing", async () => {
    let resolveSend: (() => void) | null = null;
    chatMocks.sendMessage.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveSend = resolve;
        }),
    );
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const input = await screen.findByPlaceholderText(/Ask your coach/i);
    vi.useFakeTimers();
    fireEvent.change(input, {
      target: { value: "This is a longer training update." },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    expect(screen.getByText("Thinking...")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(1600);
    });

    expect(screen.getByText("Still working...")).toBeTruthy();

    await act(() => Promise.resolve(resolveSend?.()));
  });

  it("renders persisted file parts inline so images survive a reload (issue #149)", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read"],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: true,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:05:00Z",
                messages: [
                  {
                    id: "persisted-with-image",
                    attachments: [],
                    parts: [
                      { type: "text", text: "Here's my ride summary" },
                      {
                        type: "file",
                        mediaType: "image/png",
                        filename: "ride.png",
                        url: "https://cdn.example.com/ride.png",
                      },
                    ],
                    created_at: "2026-04-04T09:01:00Z",
                    metadata: { message_kind: "user_turn" },
                    role: "user",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Here's my ride summary/);
    const image = (await screen.findByAltText("ride.png")) as HTMLImageElement;
    expect(image.src).toBe("https://cdn.example.com/ride.png");
  });

  it("renders live assistant messages from the AI SDK useChat hook", async () => {
    chatMocks.messages.push({
      id: "streamed-message-1",
      parts: [
        { text: "Keep this one easy while I shape the plan.", type: "text" },
      ],
      role: "assistant",
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Welcome back coach-side/i);
    expect(
      screen.getByText(/Keep this one easy while I shape the plan/i),
    ).toBeTruthy();
  });

  it("renders friendly live tool status from the AI SDK useChat hook", async () => {
    chatMocks.messages.push({
      id: "tool-message-1",
      parts: [
        {
          input: { user_id: "athlete-1" },
          state: "output-available",
          toolCallId: "call-1",
          type: "tool-get_athlete_context",
        },
      ],
      role: "assistant",
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: false,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Welcome back coach-side/i);
    expect(screen.getByText(/Looking up your info/i)).toBeTruthy();
    expect(screen.queryByText(/Using get_athlete_context/i)).toBeNull();
  });

  it("passes uploaded image attachments to the AI SDK message", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:activity-preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: true,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome",
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/presign") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "x-upload": "1" },
              method: "PUT",
              object_key: "uploads/activity.png",
              public_url: "https://example.com/activity.png",
              upload_url: "https://upload.example/activity.png",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/upload") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "Content-Type": "image/png" },
              method: "POST",
              object_key: "uploads/activity.png",
              public_url: "https://example.com/activity.png",
              upload_url: "",
            }),
            { status: 201 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const { container } = render(<CoachChat />);

    await screen.findByPlaceholderText(/Ask your coach/i);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["activity-image"], "activity.png", { type: "image/png" }),
        ],
      },
    });
    await screen.findByText(/Ready/i);

    const presignCall = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/chat/attachments/presign",
    );
    expect(presignCall).toBeDefined();

    const input = screen.getByPlaceholderText(/Ask your coach/i);
    fireEvent.change(input, {
      target: { value: "Please analyze this workout." },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => {
      expect(chatMocks.sendMessage).toHaveBeenCalledWith({
        id: expect.stringMatching(uuidPattern),
        parts: [
          { text: "Please analyze this workout.", type: "text" },
          {
            filename: "activity.png",
            mediaType: "image/png",
            type: "file",
            url: "https://example.com/activity.png",
          },
        ],
      });
    });
  });

  it("refuses to send a file part when the upload omits public_url and does not include a base64 fallback", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:no-public-url"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: true,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [],
              },
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/presign") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "x-upload": "1" },
              method: "PUT",
              object_key: "uploads/activity.png",
              public_url: null,
              upload_url: "https://upload.example/activity.png",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/upload") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "Content-Type": "image/png" },
              method: "POST",
              object_key: "uploads/activity.png",
              public_url: null,
              upload_url: "",
            }),
            { status: 201 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const { container } = render(<CoachChat />);

    await screen.findByPlaceholderText(/Ask your coach/i);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["activity-image"], "activity.png", { type: "image/png" }),
        ],
      },
    });

    await screen.findByText(/couldn't get a shareable link back/i);
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: /^Send$/i })
        .disabled,
    ).toBe(true);

    const input = screen.getByPlaceholderText(/Ask your coach/i);
    fireEvent.change(input, {
      target: { value: "Please analyze this workout." },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    // The strict equality on parts is the regression guard for #163: the
    // failed-upload attachment was dropped and no base64 data: URL leaked into
    // chat_messages.parts.
    await waitFor(() => {
      expect(chatMocks.sendMessage).toHaveBeenCalledWith({
        id: expect.stringMatching(uuidPattern),
        parts: [{ text: "Please analyze this workout.", type: "text" }],
      });
    });
  });

  it("sends attachment-only messages without injecting an empty text part", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: true,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [],
              },
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/presign") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "x-upload": "1" },
              method: "PUT",
              object_key: "uploads/morning-run.gpx",
              public_url: "https://example.com/morning-run.gpx",
              upload_url: "https://upload.example/morning-run.gpx",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/upload") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "Content-Type": "application/gpx+xml" },
              method: "POST",
              object_key: "uploads/morning-run.gpx",
              public_url: "https://example.com/morning-run.gpx",
              upload_url: "",
            }),
            { status: 201 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const { container } = render(<CoachChat />);

    await screen.findByPlaceholderText(/Ask your coach/i);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;

    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["<gpx>activity</gpx>"], "morning-run.gpx", {
            type: "application/gpx+xml",
          }),
        ],
      },
    });

    await screen.findByText("morning-run.gpx");
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/chat/attachments/upload",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await screen.findByText("Ready");

    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => {
      expect(chatMocks.sendMessage).toHaveBeenCalledWith({
        id: expect.stringMatching(uuidPattern),
        parts: [
          {
            filename: "morning-run.gpx",
            mediaType: "application/gpx+xml",
            type: "file",
            url: "https://example.com/morning-run.gpx",
          },
        ],
      });
    });
  });

  it("uploads GPX attachments through the chat attachment endpoint and shows a file badge", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: true,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [],
              },
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/presign") {
        expect(init?.body).toBe(
          JSON.stringify({
            content_length: 19,
            content_type: "application/gpx+xml",
            filename: "morning-run.gpx",
            purpose: "chat-attachment",
          }),
        );
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "x-upload": "1" },
              method: "PUT",
              object_key: "uploads/morning-run.gpx",
              public_url: "https://example.com/morning-run.gpx",
              upload_url: "https://upload.example/morning-run.gpx",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/upload") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "Content-Type": "application/gpx+xml" },
              method: "POST",
              object_key: "uploads/morning-run.gpx",
              public_url: "https://example.com/morning-run.gpx",
              upload_url: "",
            }),
            { status: 201 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const { container } = render(<CoachChat />);

    await screen.findByPlaceholderText(/Ask your coach/i);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    expect(fileInput.accept).toBe("image/*,application/gpx+xml,.gpx,.fit,.tcx");

    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["<gpx>activity</gpx>"], "morning-run.gpx", {
            type: "application/gpx+xml",
          }),
        ],
      },
    });

    await screen.findByText("morning-run.gpx");
    expect(screen.getByText("GPX")).toBeTruthy();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/chat/attachments/upload",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await screen.findByText("Ready");

    const input = screen.getByPlaceholderText(/Ask your coach/i);
    fireEvent.change(input, {
      target: { value: "Please parse this activity." },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => {
      expect(chatMocks.sendMessage).toHaveBeenCalledWith({
        id: expect.stringMatching(uuidPattern),
        parts: [
          { text: "Please parse this activity.", type: "text" },
          {
            filename: "morning-run.gpx",
            mediaType: "application/gpx+xml",
            type: "file",
            url: "https://example.com/morning-run.gpx",
          },
        ],
      });
    });
  });

  it("pasting an image from the clipboard attaches it via the presign upload flow", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:paste-preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: true,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: { message_kind: "welcome" },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/presign") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "x-upload": "1" },
              method: "PUT",
              object_key: "uploads/screenshot.png",
              public_url: "https://example.com/screenshot.png",
              upload_url: "https://upload.example/screenshot.png",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/attachments/upload") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              headers: { "Content-Type": "image/png" },
              method: "POST",
              object_key: "uploads/screenshot.png",
              public_url: "https://example.com/screenshot.png",
              upload_url: "",
            }),
            { status: 201 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const textarea = await screen.findByPlaceholderText(/Ask your coach/i);

    const imageFile = new File(["png-data"], "screenshot.png", {
      type: "image/png",
    });
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [
          { kind: "file", type: "image/png", getAsFile: (): File => imageFile },
        ],
      },
    });

    await screen.findByText("Ready");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/attachments/presign",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/attachments/upload",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("pasting plain text into the composer does not intercept normal text entry", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
              ],
              token_type: "Bearer",
              user_id: "athlete-1",
            }),
            { status: 200 },
          ),
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              attachments_enabled: true,
              profile_complete: true,
              thread: {
                id: "thread-1",
                user_id: "athlete-1",
                state: {},
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "Welcome back coach-side.",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: { message_kind: "welcome" },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1",
                  },
                ],
              },
            }),
            { status: 200 },
          ),
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const textarea = await screen.findByPlaceholderText(/Ask your coach/i);
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [
          { kind: "string", type: "text/plain", getAsFile: (): null => null },
        ],
      },
    });

    // No upload chip should appear — text paste doesn't trigger attachment flow
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Uploading")).toBeNull();
  });
});
