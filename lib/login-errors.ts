function classifySupabaseErrorCode(code: string): string {
  switch (code) {
    case "otp_expired":
      return "Your sign-in link has expired. Enter your email to get a new one.";
    case "bad_otp":
      return "That sign-in link is not valid. Request a new one.";
    case "email_not_confirmed":
      return "Please confirm your email address first, then try signing in.";
    case "access_denied":
      return "Access was denied. Try signing in again.";
    default:
      return "There was a problem signing you in. Try requesting a new link.";
  }
}

export function classifyQueryError(raw: string): string {
  const lower = raw.toLowerCase();
  if (
    lower.includes("missing auth code") ||
    lower.includes("already been used")
  ) {
    return "Your sign-in link is missing or has already been used. Enter your email to get a new one.";
  }
  if (
    lower.includes("unable to finish login") ||
    lower.includes("unable to finish")
  ) {
    return "Something went wrong completing your sign-in. Try again.";
  }
  return "There was a problem signing you in. Try requesting a new link.";
}

/** Returns true for error codes that indicate an expired / invalid link. */
function isLinkError(code: string): boolean {
  return (
    code === "otp_expired" || code === "bad_otp" || code === "access_denied"
  );
}

export type HashError = {
  message: string;
  /** True when the link is unusable, so the code-entry step must reset. */
  resetOtpStep: boolean;
};

/**
 * Parses a Supabase auth error from a URL hash fragment (without the `#`).
 * Supabase reports link failures there, where the server cannot see them (#34).
 */
export function parseHashError(hash: string): HashError | null {
  if (!hash) return null;
  const errorCode = new URLSearchParams(hash).get("error_code");
  if (!errorCode) return null;
  return {
    message: classifySupabaseErrorCode(errorCode),
    resetOtpStep: isLinkError(errorCode),
  };
}
