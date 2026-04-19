// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    stop: vi.fn()
  }));

  return { messages, sendMessage, setMessages, useChat };
});

vi.mock("@ai-sdk/react", () => ({
  useChat: chatMocks.useChat
}));

import { CoachChat } from "../../components/coach-chat";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  chatMocks.messages.splice(0);
  chatMocks.sendMessage.mockClear();
  chatMocks.setMessages.mockClear();
  chatMocks.useChat.mockClear();
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList);
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("CoachChat", () => {
  it("shows a login prompt when the browser session cannot mint a token", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(new Response("No browser session cookie is present.", { status: 401 }))
    ) as unknown as typeof fetch;

    render(<CoachChat />);

    await screen.findByText(/Continue with magic link/i);
    expect(screen.getByText(/Sign in to start your coaching chat/i)).toBeTruthy();
  });

  it("shows a bounded fallback error when the bootstrap request returns HTML", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        new Response("<!DOCTYPE html><html><body><h1>404</h1></body></html>", {
          status: 404,
          headers: {
            "content-type": "text/html; charset=utf-8"
          }
        })
      )
    ) as unknown as typeof fetch;

    render(<CoachChat />);

    await screen.findByText(/Continue with magic link/i);
    expect(screen.getByText(/Sign in to start your coaching chat/i)).toBeTruthy();
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
              user_id: "athlete-1"
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
        );
      }

      if (url === "/api/chat/thread") {
        return Promise.resolve(
          new Response(JSON.stringify({ detail: "Chat backend still warming up." }), {
            status: 503,
            headers: { "content-type": "application/json" }
          })
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Sorry, we're out running./i);
    expect(screen.getByText(/We'll be back soon. You've got this./i)).toBeTruthy();
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
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: "athlete-1"
            }),
            { status: 200 }
          )
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
                      message_kind: "welcome"
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1"
                  }
                ]
              }
            }),
            { status: 200 }
          )
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
        credentials: "include"
      })
    );
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
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: userId
            }),
            { status: 200 }
          )
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
                      message_kind: "welcome"
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: userId
                  }
                ]
              }
            }),
            { status: 200 }
          )
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Building your athlete profile/i);
    expect(screen.queryByText(userId)).toBeNull();
  });

  it("prefills the composer when a starter prompt is chosen", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: "athlete-1"
            }),
            { status: 200 }
          )
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
                  pending_profile_field: "goals"
                },
                created_at: "2026-04-04T09:00:00Z",
                updated_at: "2026-04-04T09:00:00Z",
                messages: [
                  {
                    id: "message-1",
                    attachments: [],
                    content: "What are your main goals for the next training block?",
                    created_at: "2026-04-04T09:00:00Z",
                    metadata: {
                      message_kind: "welcome"
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1"
                  }
                ]
              }
            }),
            { status: 200 }
          )
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const starter = await screen.findByRole("button", { name: /Generate next plan/i });
    fireEvent.click(starter);

    await waitFor(() => {
      expect(
        (screen.getByPlaceholderText(/Ask anything about your training/i) as HTMLTextAreaElement).value
      ).toBe("Build my next 14-day training plan.");
    });
  });

  it("uses an account menu instead of raw ids or switch-login copy", async () => {
    const userId = "aa687ce1-5189-4c28-bf24-e8b1574ebc5b";
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: userId
            }),
            { status: 200 }
          )
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
                      message_kind: "welcome"
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: userId
                  }
                ]
              }
            }),
            { status: 200 }
          )
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
    expect(screen.getByRole("menuitem", { name: /Profile & preferences/i })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /Sign out or change account/i })).toBeTruthy();
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
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: "athlete-1"
            }),
            { status: 200 }
          )
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
                      message_kind: "welcome"
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1"
                  }
                ]
              }
            }),
            { status: 200 }
          )
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const input = await screen.findByPlaceholderText(/Ask anything about your training/i);
    fireEvent.change(input, { target: { value: "I ran easy today." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => {
      expect(chatMocks.sendMessage).toHaveBeenCalledWith({
        parts: [{ text: "I ran easy today.", type: "text" }]
      });
    });
    expect(fetchMock).not.toHaveBeenCalledWith("/api/chat", expect.anything());
  });

  it("renders live assistant messages from the AI SDK useChat hook", async () => {
    chatMocks.messages.push({
      id: "streamed-message-1",
      parts: [{ text: "Keep this one easy while I shape the plan.", type: "text" }],
      role: "assistant"
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: "athlete-1"
            }),
            { status: 200 }
          )
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
                      message_kind: "welcome"
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1"
                  }
                ]
              }
            }),
            { status: 200 }
          )
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Welcome back coach-side/i);
    expect(screen.getByText(/Keep this one easy while I shape the plan/i)).toBeTruthy();
  });

  it("renders live tool calls from the AI SDK useChat hook", async () => {
    chatMocks.messages.push({
      id: "tool-message-1",
      parts: [
        {
          input: { ftp_watts: 300, sport: "cycling" },
          output: { zones: [] },
          state: "output-available",
          toolCallId: "call-1",
          type: "tool-calculate_zones"
        }
      ],
      role: "assistant"
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: "athlete-1"
            }),
            { status: 200 }
          )
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
                      message_kind: "welcome"
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1"
                  }
                ]
              }
            }),
            { status: 200 }
          )
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    await screen.findByText(/Welcome back coach-side/i);
    expect(screen.getByText(/calculate_zones/i)).toBeTruthy();
  });

  it("passes uploaded image attachments to the AI SDK message", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:activity-preview")
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn()
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: "athlete-1"
            }),
            { status: 200 }
          )
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
                      message_kind: "welcome"
                    },
                    role: "assistant",
                    thread_id: "thread-1",
                    user_id: "athlete-1"
                  }
                ]
              }
            }),
            { status: 200 }
          )
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
              upload_url: "https://upload.example/activity.png"
            }),
            { status: 200 }
          )
        );
      }

      if (url === "https://upload.example/activity.png") {
        return Promise.resolve(new Response(null, { status: 200 }));
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const { container } = render(<CoachChat />);

    await screen.findByPlaceholderText(/Ask anything about your training/i);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["activity-image"], "activity.png", { type: "image/png" })] }
    });
    await screen.findByText(/Ready/i);

    const input = screen.getByPlaceholderText(/Ask anything about your training/i);
    fireEvent.change(input, { target: { value: "Please analyze this workout." } });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => {
      expect(chatMocks.sendMessage).toHaveBeenCalledWith({
        parts: [
          { text: "Please analyze this workout.", type: "text" },
          {
            filename: "activity.png",
            mediaType: "image/png",
            type: "file",
            url: "https://example.com/activity.png"
          }
        ]
      });
    });
  });

  it("pasting an image from the clipboard attaches it via the presign upload flow", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:paste-preview")
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn()
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/oauth/browser-token") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "token-1",
              expires_at: "2026-04-02T08:00:00Z",
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: "athlete-1"
            }),
            { status: 200 }
          )
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
                    user_id: "athlete-1"
                  }
                ]
              }
            }),
            { status: 200 }
          )
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
              upload_url: "https://upload.example/screenshot.png"
            }),
            { status: 200 }
          )
        );
      }

      if (url === "https://upload.example/screenshot.png") {
        return Promise.resolve(new Response(null, { status: 200 }));
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const textarea = await screen.findByPlaceholderText(/Ask anything about your training/i);

    const imageFile = new File(["png-data"], "screenshot.png", { type: "image/png" });
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [{ kind: "file", type: "image/png", getAsFile: (): File => imageFile }]
      }
    });

    await screen.findByText("Ready");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/attachments/presign",
      expect.objectContaining({ method: "POST" })
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
              scopes: ["profile:read", "profile:write", "plans:read", "plans:write", "metrics:write"],
              token_type: "Bearer",
              user_id: "athlete-1"
            }),
            { status: 200 }
          )
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
                    user_id: "athlete-1"
                  }
                ]
              }
            }),
            { status: 200 }
          )
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CoachChat />);

    const textarea = await screen.findByPlaceholderText(/Ask anything about your training/i);
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [{ kind: "string", type: "text/plain", getAsFile: (): null => null }]
      }
    });

    // No upload chip should appear — text paste doesn't trigger attachment flow
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Uploading")).toBeNull();
  });
});
