// @vitest-environment jsdom

import { cleanup, render } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigationMock = vi.hoisted(() => ({ query: "" }));

vi.mock("next/navigation", () => ({
  useSearchParams: (): URLSearchParams =>
    new URLSearchParams(navigationMock.query),
}));

import ConsentPage from "../../app/consent/page";

beforeEach(() => {
  vi.stubGlobal("React", React);
  navigationMock.query = "";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function hiddenValues(container: HTMLElement): Record<string, string> {
  const values: Record<string, string> = {};
  container
    .querySelectorAll<HTMLInputElement>('input[type="hidden"]')
    .forEach((input) => {
      values[input.name] = input.value;
    });
  return values;
}

describe("ConsentPage readConsentParams", () => {
  it("reads every OAuth param into the decision form", () => {
    navigationMock.query = new URLSearchParams({
      client_id: "chatgpt",
      redirect_uri: "https://chat.example.com/cb",
      scope: "profile:read plans:write",
      state: "st-1",
      code_challenge: "chal",
      code_challenge_method: "plain",
    }).toString();

    const { container, getByText } = render(<ConsentPage />);

    expect(hiddenValues(container)).toEqual({
      client_id: "chatgpt",
      redirect_uri: "https://chat.example.com/cb",
      scope: "profile:read plans:write",
      state: "st-1",
      code_challenge: "chal",
      code_challenge_method: "plain",
    });
    expect(getByText("Client: chatgpt")).toBeTruthy();
    expect(getByText("profile:read")).toBeTruthy();
    expect(getByText("plans:write")).toBeTruthy();
  });

  it("applies defaults when no params are present", () => {
    const { container, getByText } = render(<ConsentPage />);

    expect(hiddenValues(container)).toEqual({
      client_id: "",
      redirect_uri: "",
      scope: "profile:read plans:write metrics:write",
      state: "",
      code_challenge: "",
      code_challenge_method: "S256",
    });
    expect(getByText("Client: Unknown client")).toBeTruthy();
    expect(getByText("Redirect URI: Missing redirect URI")).toBeTruthy();
    expect(container.querySelectorAll(".scope-list li")).toHaveLength(3);
  });

  it("keeps an empty scope param instead of defaulting, rendering no scopes", () => {
    navigationMock.query = "scope=";

    const { container } = render(<ConsentPage />);

    expect(hiddenValues(container)["scope"]).toBe("");
    expect(container.querySelectorAll(".scope-list li")).toHaveLength(0);
  });

  it("ignores repeated spaces in the scope list", () => {
    navigationMock.query = new URLSearchParams({
      scope: "  a   b ",
    }).toString();

    const { container } = render(<ConsentPage />);

    const items = Array.from(container.querySelectorAll(".scope-list li")).map(
      (li) => li.textContent,
    );
    expect(items).toEqual(["a", "b"]);
  });

  it("takes the first value of a repeated param and preserves encoded characters", () => {
    navigationMock.query =
      "client_id=first&client_id=second&redirect_uri=" +
      encodeURIComponent("https://x.example.com/cb?a=1&b=2");

    const { container } = render(<ConsentPage />);

    const values = hiddenValues(container);
    expect(values["client_id"]).toBe("first");
    expect(values["redirect_uri"]).toBe("https://x.example.com/cb?a=1&b=2");
  });
});
