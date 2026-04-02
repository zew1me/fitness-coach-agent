// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CoachingDashboard } from "../../components/coaching-dashboard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CoachingDashboard", () => {
  it("shows a login prompt when the browser session cannot mint a token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("No browser session cookie is present.", { status: 401 })))
    );

    render(<CoachingDashboard />);

    await screen.findByText(/same-origin bearer token/i);
    expect(screen.getByText(/No browser session cookie is present/i)).toBeTruthy();
  });

  it("loads a profile through the browser-token bridge", async () => {
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

      if (url === "/api/profile") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              user_id: "athlete-1",
              age: 34,
              cycling_ftp_watts: 250,
              goals: ["Improve repeatability"],
              constraints: ["Friday travel"],
              injuries_rehab: ["Achilles rehab"],
              notes: "Needs portable sessions.",
              weight_kg: 70.5
            }),
            { status: 200 }
          )
        );
      }

      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);
    render(<CoachingDashboard />);

    await screen.findByText(/Browser session connected/i);
    const [loadProfileButton] = screen.getAllByRole("button", { name: /load profile/i });
    expect(loadProfileButton).toBeTruthy();
    fireEvent.click(loadProfileButton as HTMLElement);

    await waitFor(() => {
      expect(screen.getByDisplayValue("250")).toBeTruthy();
    });
    expect(screen.getByDisplayValue("Friday travel")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/profile",
      expect.objectContaining({
        method: "POST",
        credentials: "include"
      })
    );
  });
});
