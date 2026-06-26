type ReleaseLeaseOptions = {
  accessToken: string;
  baseUrl: string;
  extraHeaders?: Record<string, string>;
  fetchImpl?: typeof fetch;
  leaseId: string;
  timeoutMs?: number;
};

type AcquireLeaseOptions = {
  accessToken: string;
  baseUrl: string;
  extraHeaders?: Record<string, string>;
  fetchImpl?: typeof fetch;
  leaseId: string;
  onLeaseAcquired?: () => void;
  signal?: AbortSignal;
  timeoutMs?: number;
  ttlSeconds: number;
};

type ChatTurnLeaseState = {
  thread_id?: string;
};

const RELEASE_TIMEOUT_MS = 2_000;
const ACQUIRE_TIMEOUT_MS = 10_000;

export class LeaseAcquisitionError extends Error {
  readonly status: number | undefined;

  constructor(message: string, options?: { cause?: unknown; status?: number }) {
    super(message, options);
    this.name = "LeaseAcquisitionError";
    this.status = options?.status;
  }
}

export async function acquireChatTurnLease({
  accessToken,
  baseUrl,
  extraHeaders,
  fetchImpl = fetch,
  leaseId,
  onLeaseAcquired,
  signal,
  timeoutMs = ACQUIRE_TIMEOUT_MS,
  ttlSeconds,
}: AcquireLeaseOptions): Promise<ChatTurnLeaseState> {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const requestSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;
  const response = await fetchImpl(`${baseUrl}/api/chat/model-state/lease`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      ...(extraHeaders ?? {}),
    },
    body: JSON.stringify({ lease_id: leaseId, ttl_seconds: ttlSeconds }),
    signal: requestSignal,
  });
  if (!response.ok) {
    throw new LeaseAcquisitionError(
      `Unable to acquire chat turn lease (${response.status})`,
      { status: response.status },
    );
  }
  onLeaseAcquired?.();
  try {
    return (await response.json()) as ChatTurnLeaseState;
  } catch (cause) {
    throw new LeaseAcquisitionError(
      "Unable to read acquired chat turn lease response",
      { cause },
    );
  }
}

export async function releaseChatTurnLease({
  accessToken,
  baseUrl,
  extraHeaders,
  fetchImpl = fetch,
  leaseId,
  timeoutMs = RELEASE_TIMEOUT_MS,
}: ReleaseLeaseOptions): Promise<void> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${baseUrl}/api/chat/model-state/lease`, {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
        ...(extraHeaders ?? {}),
      },
      body: JSON.stringify({ lease_id: leaseId }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Unable to release chat turn lease (${response.status})`);
    }
  } finally {
    clearTimeout(timeout);
  }
}
