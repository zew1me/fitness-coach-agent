// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", (): { useSearchParams: () => URLSearchParams } => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock(
  "../../lib/supabase",
  (): {
    getBrowserSupabaseClient: () => {
      auth: { verifyOtp: ReturnType<typeof vi.fn> };
    };
  } => ({
    getBrowserSupabaseClient: () => ({
      auth: {
        verifyOtp: vi.fn(),
      },
    }),
  }),
);

import LoginPage from "../../app/login/page";
import { siteConfig } from "../../lib/site";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  vi.stubGlobal("React", React);
  globalThis.fetch = vi.fn(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({ status: "otp_sent", inviteRequired: false }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    ),
  ) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  vi.unstubAllGlobals();
});

describe("LoginPage", () => {
  it("renders the app brand with an inline mark", () => {
    const { container } = render(<LoginPage />);

    expect(
      screen.getByRole("heading", { name: siteConfig.appName }),
    ).toBeTruthy();
    expect(container.querySelector("svg.brand-mark")).toBeTruthy();
  });

  it("requests an OTP through the server route and then shows the code entry form", async () => {
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "athlete@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send code/i }));

    await screen.findByLabelText(/6-digit code/i);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/auth/request-otp",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: "athlete@example.com",
          inviteCode: null,
          returnTo: "/consent",
        }),
      }),
    );
  });

  it("reveals the invite code field when the server requires one for a new user", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            error: "invite_required",
            message:
              "This coach is currently accepting referred athletes only. Enter your invite code to get started.",
          }),
          { status: 409, headers: { "content-type": "application/json" } },
        ),
      ),
    ) as unknown as typeof fetch;

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send code/i }));

    expect(await screen.findByLabelText("Invite code")).toBeTruthy();
    expect(
      screen.getByText(
        "This coach is currently accepting referred athletes only. Enter your invite code to get started.",
      ),
    ).toBeTruthy();
  });

  it("submits the invite code after the new-user gate is shown", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: "invite_required",
            message:
              "This coach is currently accepting referred athletes only. Enter your invite code to get started.",
          }),
          { status: 409, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ status: "otp_sent", inviteRequired: false }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send code/i }));
    fireEvent.change(await screen.findByLabelText("Invite code"), {
      target: { value: "alpha-access" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send code/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/auth/request-otp",
      expect.objectContaining({
        body: JSON.stringify({
          email: "new@example.com",
          inviteCode: "alpha-access",
          returnTo: "/consent",
        }),
      }),
    );
    expect(await screen.findByLabelText(/6-digit code/i)).toBeTruthy();
  });
});
