export const defaultReturnTo = "/consent";

export function normalizeReturnTo(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return defaultReturnTo;
  }

  if (!value.startsWith("/") || value.startsWith("//")) {
    return defaultReturnTo;
  }

  return value;
}

export function buildLoginRedirectPath(
  returnTo: string | null | undefined,
  error: string | null | undefined = null
): string {
  const params = new URLSearchParams({
    return_to: normalizeReturnTo(returnTo)
  });

  if (error) {
    params.set("error", error);
  }

  return `/login?${params.toString()}`;
}
