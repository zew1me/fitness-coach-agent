import * as Sentry from "@sentry/nextjs";

import {
  athleteProfileSchema,
  type CalendarPlannedWorkout,
  type CalendarResponse,
  calendarResponseSchema,
  chatMessagePageSchema,
  chatThreadResponseSchema,
  chatTurnLeaseStatusSchema,
  intervalsAuthorizeResponseSchema,
  intervalsConnectionStatusSchema,
  intervalsSyncResponseSchema,
  type ParsedChatMessagePage,
  type ParsedChatThreadResponse,
  type ParsedChatTurnLeaseStatus,
  resolvePlanWorkoutResponseSchema,
  uploadRequestSchema,
} from "./schemas";
import type {
  AthleteProfile,
  BrowserTokenResponse,
  FitnessMetrics,
  IntervalsConnectionStatus,
  IntervalsSyncResponse,
  PresignUploadRequest,
  PresignUploadResponse,
} from "./types";

type FetchLike = typeof fetch;

// Backoff schedule for transient fetch drops. iOS WebKit aborts an in-flight
// fetch (tab suspended, cell↔Wi-Fi handoff, low-memory kill) with a TypeError
// whose message is "Load failed"; a single silent retry recovers the request
// before the user ever sees the Coach Unavailable card.
const TRANSIENT_FETCH_RETRY_DELAYS_MS = [300, 900];

function isTransientFetchError(error: unknown): boolean {
  // Heuristic: WebKit emits `TypeError: Load failed` and other engines emit
  // `TypeError: Failed to fetch` for a network-level abort, so we retry on the
  // TypeError *type* rather than brittle message matching. The tradeoff is that
  // a genuine client-side TypeError (a programming bug) is also retried before
  // surfacing — acceptable since it is still re-thrown after retries, just
  // delayed. HTTP errors surface as plain Errors and are left alone so real
  // outages still propagate immediately.
  return error instanceof TypeError;
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const onAbort = (): void => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function withRetry<T>(
  operation: () => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  for (
    let attempt = 0;
    attempt <= TRANSIENT_FETCH_RETRY_DELAYS_MS.length;
    attempt += 1
  ) {
    try {
      return await operation();
    } catch (error) {
      const backoffMs = TRANSIENT_FETCH_RETRY_DELAYS_MS[attempt];
      if (
        backoffMs === undefined ||
        signal?.aborted ||
        !isTransientFetchError(error)
      ) {
        throw error;
      }
      // Recovery is silent for the user, so leave a breadcrumb — otherwise a
      // rising retry rate (frequent WebKit drops or a flaky backend) stays
      // invisible to monitoring while requests still appear healthy.
      Sentry.logger.debug("transient fetch retry", {
        attempt: attempt + 1,
        backoff_ms: backoffMs,
      });
      await delay(backoffMs, signal);
      // An abort that lands mid-backoff should stop here rather than burn
      // another token + thread fetch.
      if (signal?.aborted) {
        throw error;
      }
    }
  }
  // Unreachable: the loop always returns or throws above.
  throw new Error("withRetry exhausted without resolution");
}

function normalizeErrorText(detail: string): string {
  const trimmed = detail.trim();
  if (trimmed.length === 0) {
    return "The coaching backend is unavailable right now. Please try again in a moment.";
  }

  if (
    trimmed.startsWith("<") ||
    trimmed.toLowerCase().includes("<!doctype html")
  ) {
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
    Sentry.logger.warn("api request failed", {
      status: response.status,
      url: response.url,
    });
    throw new Error(detail || `Request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchBrowserToken(
  fetchImpl: FetchLike = fetch,
): Promise<BrowserTokenResponse> {
  const response = await fetchImpl("/api/oauth/browser-token", {
    method: "POST",
    credentials: "include",
  });
  const token = await readJson<BrowserTokenResponse>(response);
  Sentry.logger.debug("browser token fetched", { user_id: token.user_id });
  return token;
}

async function authorizedFetch<T>(
  path: string,
  init: RequestInit,
  fetchImpl: FetchLike = fetch,
): Promise<T> {
  const token = await fetchBrowserToken(fetchImpl);
  const headers = new Headers(init.headers ?? {});
  headers.set("Authorization", `Bearer ${token.access_token}`);
  if (
    init.body !== undefined &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetchImpl(path, {
    ...init,
    credentials: "include",
    headers,
  });

  return readJson<T>(response);
}

export async function loadProfile(
  userId: string,
  fetchImpl: FetchLike = fetch,
): Promise<AthleteProfile> {
  type SummaryResponse = { profile: AthleteProfile };
  const summary = await authorizedFetch<SummaryResponse>(
    "/api/engine/get-athlete-summary",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    },
    fetchImpl,
  );
  return summary.profile;
}

export async function loadFitnessMetrics(
  userId: string,
  fetchImpl: FetchLike = fetch,
): Promise<FitnessMetrics> {
  type SummaryResponse = { fitness_metrics: FitnessMetrics };
  const summary = await authorizedFetch<SummaryResponse>(
    "/api/engine/get-athlete-summary",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    },
    fetchImpl,
  );
  return summary.fitness_metrics;
}

export async function loadIntervalsStatus(
  fetchImpl: FetchLike = fetch,
): Promise<IntervalsConnectionStatus> {
  const raw = await authorizedFetch<unknown>(
    "/api/intervals/status",
    { method: "GET" },
    fetchImpl,
  );
  return intervalsConnectionStatusSchema.parse(raw);
}

export async function startIntervalsAuthorization(
  fetchImpl: FetchLike = fetch,
): Promise<string> {
  const raw = await authorizedFetch<unknown>(
    "/api/intervals/authorize",
    { method: "POST" },
    fetchImpl,
  );
  return intervalsAuthorizeResponseSchema.parse(raw).redirect_url;
}

export async function disconnectIntervals(
  fetchImpl: FetchLike = fetch,
): Promise<IntervalsConnectionStatus> {
  const raw = await authorizedFetch<unknown>(
    "/api/intervals/connection",
    { method: "DELETE" },
    fetchImpl,
  );
  return intervalsConnectionStatusSchema.parse(raw);
}

export async function syncIntervals(
  days = 14,
  fetchImpl: FetchLike = fetch,
): Promise<IntervalsSyncResponse> {
  const raw = await authorizedFetch<unknown>(
    "/api/intervals/sync",
    { method: "POST", body: JSON.stringify({ days }) },
    fetchImpl,
  );
  return intervalsSyncResponseSchema.parse(raw);
}

export async function confirmSportThreshold(
  userId: string,
  sport: string,
  fetchImpl: FetchLike = fetch,
): Promise<void> {
  await authorizedFetch<unknown>(
    "/api/engine/confirm-threshold",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId, sport }),
    },
    fetchImpl,
  );
}

export async function confirmProfileMetric(
  userId: string,
  metric: "max_hr" | "weight",
  fetchImpl: FetchLike = fetch,
): Promise<void> {
  await authorizedFetch<unknown>(
    "/api/engine/confirm-profile-metric",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId, metric }),
    },
    fetchImpl,
  );
}

export async function saveProfile(
  profile: AthleteProfile,
  fetchImpl: FetchLike = fetch,
): Promise<AthleteProfile> {
  const { user_id, ...fields } = athleteProfileSchema.parse(profile);
  return authorizedFetch<AthleteProfile>(
    "/api/engine/update-athlete-profile",
    {
      method: "POST",
      body: JSON.stringify({ user_id, fields }),
    },
    fetchImpl,
  );
}

export async function loadChatThread(
  fetchImpl: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ParsedChatThreadResponse> {
  const raw = await withRetry(
    () =>
      authorizedFetch<unknown>(
        "/api/chat/thread",
        { method: "GET", signal: signal ?? null },
        fetchImpl,
      ),
    signal,
  );
  const thread = chatThreadResponseSchema.parse(raw);
  Sentry.logger.debug("chat thread loaded", {
    message_count: thread.thread.messages.length,
    thread_id: thread.thread.id,
  });
  return thread;
}

export async function loadChatMessages(
  before: string,
  fetchImpl: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ParsedChatMessagePage> {
  const params = new URLSearchParams({ before, limit: "50" });
  const page = await authorizedFetch<unknown>(
    `/api/chat/messages?${params.toString()}`,
    { method: "GET", signal: signal ?? null },
    fetchImpl,
  );
  return chatMessagePageSchema.parse(page);
}

export async function loadChatTurnLeaseStatus(
  fetchImpl: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ParsedChatTurnLeaseStatus> {
  const raw = await authorizedFetch<unknown>(
    "/api/chat/model-state/lease",
    { cache: "no-store", method: "GET", signal: signal ?? null },
    fetchImpl,
  );
  return chatTurnLeaseStatusSchema.parse(raw);
}

export async function loadCalendar(
  start: string,
  end: string,
  fetchImpl: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<CalendarResponse> {
  const params = new URLSearchParams({ start, end });
  const raw = await authorizedFetch<unknown>(
    `/api/calendar?${params.toString()}`,
    { method: "GET", signal: signal ?? null },
    fetchImpl,
  );
  return calendarResponseSchema.parse(raw);
}

export async function resolvePlannedWorkout(
  planWorkoutId: string,
  outcome: "completed" | "skipped",
  fetchImpl: FetchLike = fetch,
): Promise<CalendarPlannedWorkout> {
  const raw = await authorizedFetch<unknown>(
    "/api/engine/resolve-plan-workout",
    {
      method: "POST",
      body: JSON.stringify({
        outcome,
        plan_workout_id: planWorkoutId,
        source: "athlete",
      }),
    },
    fetchImpl,
  );
  return resolvePlanWorkoutResponseSchema.parse(raw).workout;
}

export async function createChatUploadIntent(
  payload: PresignUploadRequest,
  fetchImpl: FetchLike = fetch,
): Promise<PresignUploadResponse> {
  const body = uploadRequestSchema.parse(payload);
  return authorizedFetch<PresignUploadResponse>(
    "/api/chat/attachments/presign",
    {
      method: "POST",
      body: JSON.stringify(body),
    },
    fetchImpl,
  );
}

export async function uploadFile(
  objectKey: string,
  file: File,
  fetchImpl: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PresignUploadResponse> {
  const formData = new FormData();
  formData.append("object_key", objectKey);
  formData.append("file", file);

  const filename_suffix = file.name.includes(".")
    ? file.name.slice(file.name.lastIndexOf(".")).slice(0, 16)
    : "";
  Sentry.logger.debug("attachment upload started", {
    filename_suffix,
    content_type: file.type,
    size_bytes: file.size,
  });
  const result = await authorizedFetch<PresignUploadResponse>(
    "/api/chat/attachments/upload",
    {
      method: "POST",
      body: formData,
      signal: signal ?? null,
    },
    fetchImpl,
  );
  Sentry.logger.info("attachment upload complete", {
    filename_suffix,
    object_key_suffix: result.object_key.slice(-12),
    has_public_url: result.public_url !== null,
  });
  return result;
}
