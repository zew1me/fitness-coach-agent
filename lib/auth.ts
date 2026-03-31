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
