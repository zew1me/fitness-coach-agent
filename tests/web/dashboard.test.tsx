// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CoachChat } from "../../components/coach-chat";

const originalFetch = globalThis.fetch;

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
});
