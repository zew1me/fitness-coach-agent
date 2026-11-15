// @vitest-environment jsdom

import { cleanup, render } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigationMock = vi.hoisted(() => ({ query: "" }));

vi.mock("next/navigation", () => ({
  useSearchParams: (): URLSearchParams =>
    new URLSearchParams(navigationMock.query),
}));

import DevicePage from "../../app/device/page";

beforeEach(() => {
  vi.stubGlobal("React", React);
  navigationMock.query = "";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function expectEntryForm(container: HTMLElement): void {
  const form = container.querySelector("form");
  expect(form?.getAttribute("action")).toBe("/api/oauth/device");
  expect(form?.getAttribute("method")).toBe("get");
  const input = container.querySelector<HTMLInputElement>(
    'input[name="user_code"]',
  );
  expect(input?.getAttribute("autocomplete")).toBe("off");
  expect(input?.type).toBe("text");
}

describe("DevicePage", () => {
  it("renders the code entry form by default", () => {
    const { container, getByText, queryByRole } = render(<DevicePage />);
    expect(getByText("Connect the coach CLI")).toBeTruthy();
    expectEntryForm(container);
    expect(queryByRole("alert")).toBeNull();
  });

  it("shows the invalid code error with the entry form", () => {
    navigationMock.query = "error=invalid_code";
    const { container, getByRole } = render(<DevicePage />);
    expect(getByRole("alert").textContent).toContain(
      "That code is invalid or has expired. Run `coach login` again to get a new one.",
    );
    expectEntryForm(container);
  });

  it("renders the confirm step with a POST decision form", () => {
    navigationMock.query = new URLSearchParams({
      step: "confirm",
      user_code: "BCDF-GHJK",
      client_id: "fitness-coach-cli",
      scope: "profile:read plans:write",
    }).toString();
    const { container, getByText } = render(<DevicePage />);

    expect(getByText("BCDF-GHJK")).toBeTruthy();
    expect(getByText("Client: Coach CLI (fitness-coach-cli)")).toBeTruthy();
    expect(
      Array.from(container.querySelectorAll(".scope-list li")).map(
        (li) => li.textContent,
      ),
    ).toEqual(["profile:read", "plans:write"]);
    expect(
      getByText(/Only approve if you started `coach login` yourself/),
    ).toBeTruthy();

    const form = container.querySelector("form");
    expect(form?.getAttribute("action")).toBe("/api/oauth/device/decision");
    expect(form?.getAttribute("method")).toBe("post");
    expect(
      container.querySelector<HTMLInputElement>('input[type="hidden"]')?.value,
    ).toBe("BCDF-GHJK");
    const buttons = Array.from(
      container.querySelectorAll<HTMLButtonElement>("button[name=decision]"),
    ).map((b) => [b.value, b.textContent]);
    expect(buttons).toEqual([
      ["approve", "Approve"],
      ["deny", "Deny"],
    ]);
  });

  it("shows the raw client id for unknown clients", () => {
    navigationMock.query =
      "step=confirm&user_code=BCDF-GHJK&client_id=other-client&scope=a";
    const { getByText } = render(<DevicePage />);
    expect(getByText("Client: other-client")).toBeTruthy();
  });

  it("falls back to the entry form when confirm has no code", () => {
    navigationMock.query = "step=confirm";
    const { container } = render(<DevicePage />);
    expectEntryForm(container);
  });

  it("renders the approved state", () => {
    navigationMock.query = "status=approved";
    const { getByText, container } = render(<DevicePage />);
    expect(
      getByText(
        "CLI connected. You can close this tab and return to your terminal.",
      ),
    ).toBeTruthy();
    expect(container.querySelector("form")).toBeNull();
  });

  it("renders the denied state", () => {
    navigationMock.query = "status=denied";
    const { getByText } = render(<DevicePage />);
    expect(getByText("Request denied. Nothing was connected.")).toBeTruthy();
  });

  it("ignores unknown status and error values", () => {
    navigationMock.query = "status=<b>x</b>&error=weird";
    const { container, queryByRole } = render(<DevicePage />);
    expectEntryForm(container);
    expect(queryByRole("alert")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });
});
