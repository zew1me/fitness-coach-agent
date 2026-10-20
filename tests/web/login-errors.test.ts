import { describe, expect, it } from "vitest";

import { classifyQueryError, parseHashError } from "../../lib/login-errors";

describe("parseHashError", () => {
  it("returns null for an empty hash or one without error_code", () => {
    expect(parseHashError("")).toBeNull();
    expect(parseHashError("access_token=abc")).toBeNull();
  });

  it("maps link errors and resets the code-entry step", () => {
    expect(parseHashError("error=x&error_code=otp_expired")).toEqual({
      message:
        "Your sign-in link has expired. Enter your email to get a new one.",
      resetOtpStep: true,
    });
  });

  it("keeps the code-entry step for non-link errors", () => {
    expect(parseHashError("error_code=email_not_confirmed")).toEqual({
      message: "Please confirm your email address first, then try signing in.",
      resetOtpStep: false,
    });
  });

  it("falls back to a generic message for unknown codes", () => {
    expect(parseHashError("error_code=weird")?.message).toBe(
      "There was a problem signing you in. Try requesting a new link.",
    );
  });
});

describe("classifyQueryError", () => {
  it("classifies known callback errors", () => {
    expect(classifyQueryError("Missing auth code")).toContain("already");
    expect(classifyQueryError("Unable to finish login")).toContain("Try again");
  });
});
