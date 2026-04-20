import {
  athleteProfileSchema,
  chatMessageSchema,
  uploadRequestSchema
} from "./schemas";
import type {
  AthleteProfile,
  BrowserTokenResponse,
  ChatAttachment,
  ChatThreadResponse,
  FitnessMetrics,
  PresignUploadRequest,
  PresignUploadResponse
} from "./types";

type FetchLike = typeof fetch;

function normalizeErrorText(detail: string): string {
  const trimmed = detail.trim();
  if (trimmed.length === 0) {
    return "The coaching backend is unavailable right now. Please try again in a moment.";
  }

  if (trimmed.startsWith("<") || trimmed.toLowerCase().includes("<!doctype html")) {
    return "The coaching backend is unavailable right now. Please try again in a moment.";
  }

  const collapsed = trimmed.replace(/\s+/g, " ");
  return collapsed.length > 200 ? `${collapsed.slice(0, 197)}...` : collapsed;
}

async function readErrorDetail(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") ?? "";

  if (contentType.includes("application/json")) {
    try {
      const payload = (await response.json()) as Record<string, unknown>;
      const detail =
        typeof payload["detail"] === "string"
          ? payload["detail"]
          : typeof payload["message"] === "string"
            ? payload["message"]
            : typeof payload["error"] === "string"
              ? payload["error"]
              : "";
      return normalizeErrorText(detail);
    } catch {
      return "The coaching backend is unavailable right now. Please try again in a moment.";
    }
  }

  return normalizeErrorText(await response.text());
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || `Request failed with status ${response.status}`);
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
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
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
  type SummaryResponse = { profile: AthleteProfile };
  const summary = await authorizedFetch<SummaryResponse>(
    "/api/engine/get-athlete-summary",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId })
    },
    fetchImpl
  );
  return summary.profile;
}

export async function loadFitnessMetrics(
  userId: string,
  fetchImpl: FetchLike = fetch
): Promise<FitnessMetrics> {
  type SummaryResponse = { fitness_metrics: FitnessMetrics };
  const summary = await authorizedFetch<SummaryResponse>(
    "/api/engine/get-athlete-summary",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId })
    },
    fetchImpl
  );
  return summary.fitness_metrics;
}

export async function confirmSportThreshold(
  userId: string,
  sport: string,
  fetchImpl: FetchLike = fetch
): Promise<void> {
  await authorizedFetch<unknown>(
    "/api/engine/confirm-threshold",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId, sport })
    },
    fetchImpl
  );
}

export async function confirmProfileMetric(
  userId: string,
  metric: "max_hr" | "weight",
  fetchImpl: FetchLike = fetch
): Promise<void> {
  await authorizedFetch<unknown>(
    "/api/engine/confirm-profile-metric",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId, metric })
    },
    fetchImpl
  );
}

export async function saveProfile(
  profile: AthleteProfile,
  fetchImpl: FetchLike = fetch
): Promise<AthleteProfile> {
  const { user_id, ...fields } = athleteProfileSchema.parse(profile);
  return authorizedFetch<AthleteProfile>(
    "/api/engine/update-athlete-profile",
    {
      method: "POST",
      body: JSON.stringify({ user_id, fields })
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

export async function loadChatThread(fetchImpl: FetchLike = fetch): Promise<ChatThreadResponse> {
  return authorizedFetch<ChatThreadResponse>(
    "/api/chat/thread",
    {
      method: "GET"
    },
    fetchImpl
  );
}

export async function sendChatMessage(
  payload: {
    attachments?: ChatAttachment[];
    content: string;
  },
  fetchImpl: FetchLike = fetch
): Promise<ChatThreadResponse> {
  const body = chatMessageSchema.parse({
    content: payload.content,
    attachments: payload.attachments ?? []
  });
  return authorizedFetch<ChatThreadResponse>(
    "/api/chat/messages",
    {
      method: "POST",
      body: JSON.stringify(body)
    },
    fetchImpl
  );
}

export async function createChatUploadIntent(
  payload: PresignUploadRequest,
  fetchImpl: FetchLike = fetch
): Promise<PresignUploadResponse> {
  const body = uploadRequestSchema.parse(payload);
  return authorizedFetch<PresignUploadResponse>(
    "/api/chat/attachments/presign",
    {
      method: "POST",
      body: JSON.stringify(body)
    },
    fetchImpl
  );
}

export async function uploadFile(
  objectKey: string,
  file: File,
  fetchImpl: FetchLike = fetch
): Promise<PresignUploadResponse> {
  const formData = new FormData();
  formData.append("object_key", objectKey);
  formData.append("file", file);

  return authorizedFetch<PresignUploadResponse>(
    "/api/chat/attachments/upload",
    {
      method: "POST",
      body: formData
    },
    fetchImpl
  );
}
