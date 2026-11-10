export const defaultReturnTo = "/consent";

export function normalizeReturnTo(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return defaultReturnTo;
  }

  // URL parsing treats backslashes as slashes and removes control characters;
  // reject them before checking the local path to prevent disguised redirects.
  const hasUnsafeCharacters = [...value].some(
    (character) =>
      character === "\\" ||
      character.charCodeAt(0) < 32 ||
      character.charCodeAt(0) === 127,
  );
  if (!value.startsWith("/") || value.startsWith("//") || hasUnsafeCharacters) {
    return defaultReturnTo;
  }

  return value;
}

export function buildLoginRedirectPath(
  returnTo: string | null | undefined,
  error: string | null | undefined = null,
): string {
  const params = new URLSearchParams({
    return_to: normalizeReturnTo(returnTo),
  });

  if (error) {
    params.set("error", error);
  }

  return `/login?${params.toString()}`;
}
