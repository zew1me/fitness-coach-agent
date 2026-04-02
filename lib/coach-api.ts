import {
  athleteProfileSchema,
  planRequestSchema,
  uploadRequestSchema
} from "./schemas";
import type {
  AthleteProfile,
  BrowserTokenResponse,
  CheckInResponse,
  GeneratedPlanResponse,
  PresignUploadRequest,
  PresignUploadResponse
} from "./types";

type FetchLike = typeof fetch;

function normalizeErrorDetail(response: Response, detail: string): string {
  const contentType = response.headers.get("content-type") ?? "";
  const trimmed = detail.trim();

  if (trimmed === "") {
    return `Request failed with status ${response.status}`;
  }

  if (
    contentType.includes("text/html")
    || /^<!doctype html/i.test(trimmed)
    || /^<html/i.test(trimmed)
  ) {
    if (response.status === 404) {
      return "The signed-in app shell is not available on this deployment yet.";
    }
    return `Request failed with status ${response.status}`;
  }

  return trimmed;
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(normalizeErrorDetail(response, detail));
  }
  return (await response.json()) as T;
}

export async function fetchBrowserToken(fetchImpl: FetchLike = fetch): Promise<BrowserTokenResponse> {
  const response = await fetchImpl("/api/oauth/browser-token", {
    method: "POST",
    credentials: "include"
  });
  return readJson<BrowserTokenResponse>(response);
}

async function authorizedFetch<T>(
  path: string,
  init: RequestInit,
  fetchImpl: FetchLike = fetch
): Promise<T> {
  const token = await fetchBrowserToken(fetchImpl);
  const headers = new Headers(init.headers ?? {});
  headers.set("Authorization", `Bearer ${token.access_token}`);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetchImpl(path, {
    ...init,
    credentials: "include",
    headers
  });

  return readJson<T>(response);
}

export function parseListInput(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

export async function loadProfile(userId: string, fetchImpl: FetchLike = fetch): Promise<AthleteProfile> {
  return authorizedFetch<AthleteProfile>(
    "/api/profile",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId })
    },
    fetchImpl
  );
}

export async function saveProfile(
  profile: AthleteProfile,
  fetchImpl: FetchLike = fetch
): Promise<AthleteProfile> {
  const payload = athleteProfileSchema.parse(profile);
  return authorizedFetch<AthleteProfile>(
    "/api/profile",
    {
      method: "PUT",
      body: JSON.stringify(payload)
    },
    fetchImpl
  );
}

export async function submitCheckIn(
  payload: Parameters<typeof planRequestSchema.parse>[0],
  fetchImpl: FetchLike = fetch
): Promise<CheckInResponse> {
  const body = planRequestSchema.parse(payload);
  return authorizedFetch<CheckInResponse>(
    "/api/check-ins",
    {
      method: "POST",
      body: JSON.stringify(body)
    },
    fetchImpl
  );
}

export async function generatePlan(
  payload: Parameters<typeof planRequestSchema.parse>[0],
  fetchImpl: FetchLike = fetch
): Promise<GeneratedPlanResponse> {
  const body = planRequestSchema.parse(payload);
  return authorizedFetch<GeneratedPlanResponse>(
    "/api/plans/generate",
    {
      method: "POST",
      body: JSON.stringify(body)
    },
    fetchImpl
  );
}

export async function createUploadIntent(
  payload: PresignUploadRequest,
  fetchImpl: FetchLike = fetch
): Promise<PresignUploadResponse> {
  const body = uploadRequestSchema.parse(payload);
  return authorizedFetch<PresignUploadResponse>(
    "/api/files/presign-upload",
    {
      method: "POST",
      body: JSON.stringify(body)
    },
    fetchImpl
  );
}
