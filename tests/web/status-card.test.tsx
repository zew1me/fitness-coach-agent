// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NotFound from "../../app/not-found";
import { StatusCard } from "../../components/status-card";

beforeEach(() => {
  vi.stubGlobal("React", React);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StatusCard", () => {
  it("renders an accessible status with composed content", () => {
    render(
      <StatusCard
        body="Try the request again in a moment."
        headingLevel="h1"
        role="alert"
        title="Coach unavailable"
      >
        <button type="button">Retry</button>
      </StatusCard>,
    );

    expect(
      screen.getByRole("alert", { name: "Coach unavailable" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("heading", { level: 1, name: "Coach unavailable" }),
    ).toBeTruthy();
    expect(screen.getByText("Try the request again in a moment.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("provides the app not-found state", () => {
    render(<NotFound />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Page not found" }),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "Return to coach" })
        .getAttribute("href"),
    ).toBe("/");
  });
});
